#!/usr/bin/env python3
"""Scan git history to extract domain knowledge from commit patterns.

Knowledge sources in git:
- revert commits → anti_pattern
- fix/bugfix commits → debug_playbook  
- config/yaml changes → architecture_decision, tool_preference
- meaningful commit messages → domain_rule, code_pattern
- sequential changes to same file → evolution of approach
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import get_db, init_db, save_knowledge
from extract.dedup import is_duplicate

console = Console()


# --- Git commands ---

def git_log(repo_path: str, max_commits: int = 200) -> list[dict]:
    """Get git log with commit messages."""
    result = subprocess.run(
        ["git", "log", f"--max-count={max_commits}",
         "--pretty=format:%H|||%s|||%an|||%ai|||%b",
         "--no-merges"],
        cwd=repo_path, capture_output=True, text=True
    )
    if result.returncode != 0:
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) >= 4:
            commits.append({
                "hash": parts[0],
                "subject": parts[1],
                "author": parts[2],
                "date": parts[3],
                "body": parts[4] if len(parts) > 4 else "",
            })
    return commits


def git_diff_stat(repo_path: str, commit_hash: str) -> str:
    """Get diff stat for a commit."""
    result = subprocess.run(
        ["git", "diff", "--stat", f"{commit_hash}~1", commit_hash],
        cwd=repo_path, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def git_diff(repo_path: str, commit_hash: str,
             file_filter: Optional[str] = None,
             max_lines: int = 100) -> str:
    """Get diff content for a commit, optionally filtered by file."""
    cmd = ["git", "diff", f"{commit_hash}~1", commit_hash,
           "--unified=2", f"--stat"]
    if file_filter:
        cmd.extend(["--", file_filter])

    result = subprocess.run(
        ["git", "diff", f"{commit_hash}~1", commit_hash, "--unified=3"],
        cwd=repo_path, capture_output=True, text=True
    )
    if result.returncode != 0:
        return ""

    lines = result.stdout.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} lines truncated)"
    return result.stdout


def git_show_file_at_commit(repo_path: str, commit_hash: str, filepath: str) -> str:
    """Show file content at a specific commit."""
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:{filepath}"],
        cwd=repo_path, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


# --- Signal detection from commits ---

# Commit message patterns that indicate high-value knowledge
REVERT_PATTERNS = [
    r"^[Rr]evert\b",
    r"^[Uu]ndo\b",
    r"롤백",
    r"되돌",
]

FIX_PATTERNS = [
    r"^[Ff]ix[:\s]",
    r"^[Bb]ugfix[:\s]",
    r"^[Hh]otfix[:\s]",
    r"수정[:\s]",
    r"버그\s*수정",
    r"오류\s*수정",
]

DECISION_PATTERNS = [
    r"대신|instead\s+of",
    r"변경|[Cc]hange\s+to",
    r"교체|[Rr]eplace",
    r"개선|[Ii]mprove",
    r"[Rr]efactor",
    r"[Mm]igrate",
    r"절대|[Nn]ever|[Aa]lways",
    r"금지|[Dd]o\s+not|[Dd]on't",
]

CONFIG_FILE_PATTERNS = [
    r"\.ya?ml$",
    r"\.toml$",
    r"\.json$",
    r"\.env",
    r"config",
    r"settings",
    r"Dockerfile",
    r"requirements",
]


def classify_commit(commit: dict) -> Optional[dict]:
    """Classify a commit's knowledge value.
    
    Returns None if low-value, otherwise a dict with:
        type, signal_strength, reason
    """
    subject = commit["subject"]
    body = commit.get("body", "")
    full_msg = f"{subject} {body}"

    # Revert → anti_pattern (highest value)
    for p in REVERT_PATTERNS:
        if re.search(p, subject):
            return {
                "type": "anti_pattern",
                "signal_strength": "high",
                "reason": f"Revert commit: {subject}",
            }

    # Fix → debug_playbook
    for p in FIX_PATTERNS:
        if re.search(p, subject):
            return {
                "type": "debug_playbook",
                "signal_strength": "medium",
                "reason": f"Fix commit: {subject}",
            }

    # Decision language → architecture_decision or domain_rule
    for p in DECISION_PATTERNS:
        if re.search(p, full_msg, re.IGNORECASE):
            return {
                "type": "architecture_decision",
                "signal_strength": "medium",
                "reason": f"Decision signal: {subject}",
            }

    # Short/generic messages → skip
    if len(subject) < 15 or subject.lower() in ("wip", "update", "minor", "cleanup"):
        return None

    # Long commit body often contains reasoning
    if len(body) > 100:
        return {
            "type": "domain_rule",
            "signal_strength": "low",
            "reason": f"Detailed commit message ({len(body)} chars)",
        }

    return None


def detect_config_changes(repo_path: str, commit: dict) -> list[str]:
    """Check if a commit changes config/yaml files."""
    stat = git_diff_stat(repo_path, commit["hash"])
    changed_configs = []
    for line in stat.split("\n"):
        for pattern in CONFIG_FILE_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                # Extract filename
                filename = line.split("|")[0].strip()
                if filename:
                    changed_configs.append(filename)
    return changed_configs


# --- Main scanner ---

def scan_git_history(repo_path: str, max_commits: int = 200,
                     dry_run: bool = False) -> list[dict]:
    """Scan git history and extract knowledge candidates.
    
    Returns list of raw candidates (not yet LLM-structured).
    For MVP, uses rule-based extraction without LLM calls.
    """
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        console.print(f"[red]Not a git repository: {repo}[/red]")
        return []

    console.print(f"[bold]Scanning git history: {repo}[/bold]")

    commits = git_log(str(repo), max_commits)
    if not commits:
        console.print("[yellow]No commits found.[/yellow]")
        return []

    console.print(f"[dim]Analyzing {len(commits)} commits...[/dim]")

    candidates = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning commits...", total=len(commits))

        for commit in commits:
            progress.update(task, advance=1)
            classification = classify_commit(commit)

            if not classification:
                continue

            # Get diff for context
            diff = git_diff(str(repo), commit["hash"], max_lines=80)
            config_changes = detect_config_changes(str(repo), commit)

            candidate = {
                "commit_hash": commit["hash"][:8],
                "commit_date": commit["date"],
                "commit_subject": commit["subject"],
                "commit_body": commit.get("body", ""),
                "diff_excerpt": diff[:2000] if diff else "",
                "config_files_changed": config_changes,
                **classification,
            }
            candidates.append(candidate)

    console.print(f"[green]Found {len(candidates)} knowledge candidate(s)[/green]")

    # Show summary
    if candidates:
        by_type = {}
        for c in candidates:
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
        for t, count in sorted(by_type.items()):
            console.print(f"  {t}: {count}")

    return candidates


def scan_and_save(repo_path: str, max_commits: int = 200,
                  auto_approve: bool = False, dry_run: bool = False):
    """Scan git and save candidates to Mojo DB.
    
    For MVP: saves commit-based candidates as-is (rule-based extraction).
    Full version: pipes candidates through LLM structuring pipeline.
    """
    candidates = scan_git_history(repo_path, max_commits, dry_run)

    if not candidates or dry_run:
        return

    init_db()
    db = get_db()
    existing = db.execute(
        "SELECT content FROM knowledge WHERE archived = 0"
    ).fetchall()
    existing_contents = [r[0] for r in existing]

    saved = 0
    skipped = 0

    for i, cand in enumerate(candidates):
        # Build content from commit info
        subject = cand["commit_subject"].strip()
        body = cand["commit_body"].strip()
        if body:
            # Drop leading duplication of subject in body (common in git trailers)
            if body.startswith(subject):
                body = body[len(subject):].lstrip(" .:-\n")
            content = body if body else subject
        else:
            content = subject

        # Dedup check
        is_dup, sim = is_duplicate(content, existing_contents, threshold=0.65)
        if is_dup:
            skipped += 1
            continue

        # Derive domain from repo path or file paths
        repo_name = Path(repo_path).name.lower()
        domain = _infer_domain(repo_name, cand.get("config_files_changed", []))

        # Generate ID
        kid = f"git-{cand['commit_hash']}"

        knowledge = {
            "id": kid,
            "type": cand["type"],
            "domain": domain,
            "title": cand["commit_subject"][:50],
            "content": content[:500],
            "status": "detail",
            "parent_id": None,
            "detail_ids": [],
            "reasoning": (
                f"Auto-extracted from git {cand['type']} commit {cand['commit_hash']} "
                f"({cand['commit_date'][:10]}). "
                f"Signal: {cand['reason']}. "
                f"Original message: {cand['commit_subject'][:80]}"
            ),
            "confidence": _signal_to_confidence(cand["signal_strength"]),
            "source_session_id": f"git-scan-{cand['commit_hash']}",
            "related_ids": [],
            "tags": ["git-extracted", cand["type"]],
            "approved": 1 if auto_approve else 0,
        }

        save_knowledge(db, knowledge)
        existing_contents.append(content)
        saved += 1

    db.close()

    console.print(f"\n[bold green]Saved {saved} items, "
                  f"skipped {skipped} duplicates.[/bold green]")
    if not auto_approve and saved > 0:
        console.print("[dim]Run `python review.py` to approve extracted items.[/dim]")


def _infer_domain(repo_name: str, changed_files: list[str]) -> str:
    """Infer a domain bucket from repo name and changed files.

    Rough heuristic: look at the kinds of files that were touched to pick
    a broad domain. Projects that don't match anything fall back to
    ``project/<repo_name>``.
    """
    for f in changed_files:
        low = f.lower()
        if "model" in low or "train" in low or "notebook" in low:
            return "ml/general"
        if "api" in low or "server" in low or "router" in low:
            return "web/general"
        if "docker" in low or "k8s" in low or "deploy" in low:
            return "infra/general"

    return f"project/{repo_name}"


def _signal_to_confidence(strength: str) -> float:
    return {"high": 0.85, "medium": 0.65, "low": 0.45}.get(strength, 0.5)


# --- Folder scanner ---

def scan_project_folder(project_path: str) -> list[dict]:
    """Scan a project folder for existing knowledge artifacts.
    
    Looks for:
    - CLAUDE.md files (existing Claude Code context)
    - README.md (project documentation)
    - Config/YAML files (encoded decisions)
    - .claude/ directory (past session transcripts)
    """
    root = Path(project_path).resolve()
    found_sources = []

    # 1. CLAUDE.md
    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        claude_md = root / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        found_sources.append({
            "source": "claude_md",
            "path": str(claude_md),
            "content": claude_md.read_text(encoding="utf-8", errors="ignore")[:5000],
        })

    # 2. README
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        readme = root / name
        if readme.exists():
            found_sources.append({
                "source": "readme",
                "path": str(readme),
                "content": readme.read_text(encoding="utf-8", errors="ignore")[:5000],
            })
            break

    # 3. Config files
    for pattern in ["*.yaml", "*.yml", "*.toml", "*.ini"]:
        for cfg in root.rglob(pattern):
            # Skip node_modules, venv, .git
            if any(skip in str(cfg) for skip in
                   ["node_modules", "venv", ".venv", ".git", "__pycache__"]):
                continue
            try:
                content = cfg.read_text(encoding="utf-8", errors="ignore")
                if len(content) > 50:  # Skip trivial files
                    found_sources.append({
                        "source": "config",
                        "path": str(cfg.relative_to(root)),
                        "content": content[:3000],
                    })
            except (OSError, UnicodeDecodeError):
                continue

    # 4. Past Claude Code sessions
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        session_count = 0
        for session_dir in claude_projects.iterdir():
            if session_dir.is_dir():
                sessions_subdir = session_dir / "sessions"
                jsonl_dir = session_dir
                # Check both structures
                for search_dir in [sessions_subdir, jsonl_dir]:
                    if search_dir.exists():
                        for jsonl in search_dir.glob("*.jsonl"):
                            found_sources.append({
                                "source": "claude_session",
                                "path": str(jsonl),
                                "content": "",  # Don't load full content yet
                            })
                            session_count += 1

        if session_count:
            console.print(f"[dim]Found {session_count} past Claude Code session(s)[/dim]")

    return found_sources


def scan_folder_and_report(project_path: str):
    """Scan a project folder and report what knowledge sources exist."""
    sources = scan_project_folder(project_path)

    if not sources:
        console.print("[yellow]No knowledge sources found.[/yellow]")
        return

    console.print(f"\n[bold]Found {len(sources)} knowledge source(s):[/bold]\n")

    by_type = {}
    for s in sources:
        by_type.setdefault(s["source"], []).append(s)

    source_labels = {
        "claude_md": "📄 CLAUDE.md files",
        "readme": "📖 README files",
        "config": "⚙️  Config/YAML files",
        "claude_session": "💬 Past Claude Code sessions",
    }

    for source_type, items in by_type.items():
        label = source_labels.get(source_type, source_type)
        console.print(f"  {label}: {len(items)}")
        for item in items[:5]:  # Show first 5
            console.print(f"    [dim]{item['path']}[/dim]")
        if len(items) > 5:
            console.print(f"    [dim]... and {len(items) - 5} more[/dim]")

    console.print(
        f"\n[bold]Next steps:[/bold]\n"
        f"  • Git scan:    python scan.py git {project_path}\n"
        f"  • Extract sessions: python -m extract.pipeline\n"
        f"  • Then sync:   python -m serve.sync --project {project_path}"
    )


# --- CLI ---

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Scan project folders and git history for domain knowledge"
    )
    sub = parser.add_subparsers(dest="cmd")

    # mojo scan folder <path>
    folder_p = sub.add_parser("folder", help="Scan project folder for knowledge sources")
    folder_p.add_argument("path", nargs="?", default=".", help="Project path")

    # mojo scan git <path>
    git_p = sub.add_parser("git", help="Extract knowledge from git history")
    git_p.add_argument("path", nargs="?", default=".", help="Git repo path")
    git_p.add_argument("--max-commits", type=int, default=200, help="Max commits to scan")
    git_p.add_argument("--auto-approve", action="store_true",
                       help="Auto-approve extracted items")
    git_p.add_argument("--dry-run", action="store_true",
                       help="Only show candidates, don't save")

    # mojo scan sessions
    sess_p = sub.add_parser("sessions", help="Backfill from past Claude Code sessions")
    sess_p.add_argument("--max-sessions", type=int, default=50)
    sess_p.add_argument(
        "--project", "-p",
        help="Only register sessions from this project path "
             "(defaults to the current working directory). Use "
             "'--project all' to backfill every project on disk.",
    )

    args = parser.parse_args()

    if args.cmd == "folder":
        scan_folder_and_report(args.path)
    elif args.cmd == "git":
        if args.dry_run:
            scan_git_history(args.path, args.max_commits)
        else:
            scan_and_save(args.path, args.max_commits, args.auto_approve)
    elif args.cmd == "sessions":
        if args.project and args.project.lower() == "all":
            proj: Optional[str] = None
        else:
            proj = args.project or str(Path.cwd())
        backfill_sessions(args.max_sessions, project_path=proj)
    else:
        parser.print_help()


def _encoded_project_dir(project_path: str) -> Path:
    """Return the ~/.claude/projects/<encoded> directory for a project path.

    Claude Code encodes project paths into its per-project transcript
    directory by replacing every ``/`` **and** ``_`` with ``-`` (verified
    empirically against Claude Code ≥ 2.0). Example:
    ``/workspace/Desktop/cloud_forecasting`` →
    ``-workspace-Desktop-cloud-forecasting``.
    """
    abs_path = str(Path(project_path).expanduser().resolve())
    encoded = abs_path.replace("/", "-").replace("_", "-")
    return Path.home() / ".claude" / "projects" / encoded


def _iter_session_dirs(project_path: Optional[str]) -> list[Path]:
    """Yield Claude Code per-project session directories to scan.

    - If ``project_path`` is None → every subdirectory under
      ``~/.claude/projects`` (explicit opt-in via ``--project all``).
    - Otherwise → only the directory that Claude Code created for this
      project, if it exists. Mojo's own repo is skipped so scanning does
      not feed its own development sessions back into a user's store.
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return []

    mojo_repo = Path(__file__).resolve().parent
    mojo_encoded = mojo_repo.name  # fallback; full match below
    mojo_encoded_full = str(mojo_repo).replace("/", "-")

    if project_path is None:
        dirs = [
            d for d in claude_projects.iterdir()
            if d.is_dir() and d.name != mojo_encoded_full
        ]
        return dirs

    target = _encoded_project_dir(project_path)
    return [target] if target.exists() else []


def backfill_sessions(max_sessions: int = 50,
                      project_path: Optional[str] = None):
    """Register past Claude Code sessions for extraction.

    When ``project_path`` is given (default: cwd), only sessions from that
    project's Claude Code transcript directory are registered. Pass
    ``project_path=None`` explicitly (``--project all`` on the CLI) to
    backfill every project on the machine — this is the old global
    behaviour and should be used deliberately.
    """
    session_dirs = _iter_session_dirs(project_path)
    if not session_dirs:
        if project_path:
            console.print(
                f"[yellow]No Claude Code sessions found for "
                f"{project_path}[/yellow]\n"
                f"[dim]Expected transcript dir: "
                f"{_encoded_project_dir(project_path)}[/dim]"
            )
        else:
            console.print("[yellow]No Claude Code sessions found.[/yellow]")
        return

    init_db()
    db = get_db()
    registered = 0

    for project_dir in session_dirs:
        # Check both session structures (legacy vs current)
        for search_dir in [project_dir / "sessions", project_dir]:
            if not search_dir.exists():
                continue
            for jsonl in sorted(search_dir.glob("*.jsonl"),
                                key=lambda p: p.stat().st_mtime,
                                reverse=True):
                if registered >= max_sessions:
                    break

                session_id = jsonl.stem
                db.execute("""
                    INSERT OR IGNORE INTO raw_sessions
                    (id, transcript_path, project_path)
                    VALUES (?, ?, ?)
                """, (session_id, str(jsonl), str(project_dir)))
                registered += 1

    db.commit()
    db.close()

    scope = f" from {project_path}" if project_path else " (all projects)"
    console.print(
        f"[green]Registered {registered} past session(s){scope} "
        f"for extraction.[/green]"
    )
    console.print("[dim]Run `mojo extract` to extract knowledge.[/dim]")


if __name__ == "__main__":
    main()
