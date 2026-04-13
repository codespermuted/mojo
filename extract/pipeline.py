"""Main extraction pipeline: Session JSONL → Structured knowledge."""

import json
import os
import sys
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Load ANTHROPIC_API_KEY (and friends) from a local .env if present.

    Looks for .env in the current working directory and then walks up to
    the project root. Does not override variables already set in the
    environment. Minimal parser — no python-dotenv dependency.
    """
    seen: set[Path] = set()
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        candidate = base / ".env"
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            pass


_load_dotenv()

import anthropic
from rich.console import Console
from rich.table import Table

# Resolve imports whether run as module or script
sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (
    get_db, get_all_knowledge, get_pending_sessions,
    mark_session_extracted, save_knowledge, log_extraction_cost
)
from extract.parser import parse_session, turns_to_conversation_text
from extract.signals import score_session_value
from extract.dedup import is_duplicate, find_related

console = Console()

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.xml").read_text(encoding="utf-8")


def run_filter(client: anthropic.Anthropic, transcript_text: str,
               model: str = "claude-haiku-4-5-20251001") -> dict:
    """Stage 1: Haiku filters for knowledge candidates."""
    prompt_template = load_prompt("filter")
    prompt = prompt_template.replace("{transcript}", transcript_text)

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    # Parse response
    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    usage = response.usage
    result = json.loads(text)
    result["_usage"] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    return result


def run_structure(client: anthropic.Anthropic, candidate: dict,
                  excerpt: str, existing_knowledge: list[dict],
                  model: str = "claude-sonnet-4-6") -> dict:
    """Stage 2: Sonnet structures a candidate into a knowledge entry."""
    prompt_template = load_prompt("structure")

    # Format existing knowledge for context
    existing_summary = ""
    if existing_knowledge:
        lines = [f"- [{k['id']}] {k['title']}: {k['content'][:100]}"
                 for k in existing_knowledge[:10]]
        existing_summary = "\n".join(lines)

    prompt = (prompt_template
              .replace("{candidate_type}", candidate["type"])
              .replace("{candidate_signal}", candidate["signal"])
              .replace("{candidate_brief}", candidate["brief"])
              .replace("{excerpt}", excerpt)
              .replace("{existing_knowledge}", existing_summary or "(none)"))

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    usage = response.usage
    result = json.loads(text)
    result["_usage"] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    return result


def extract_session(session_path: str, session_id: str,
                    dry_run: bool = False) -> list[dict]:
    """Full extraction pipeline for a single session.
    
    Returns list of extracted knowledge items.
    """
    db = get_db()
    client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    extracted = []

    # 1. Parse transcript
    console.print(f"[dim]Parsing: {session_path}[/dim]")
    session_data = parse_session(session_path)

    if session_data["turn_count"] < 5:
        console.print("[yellow]Skipped: too few turns[/yellow]")
        mark_session_extracted(db, session_id)
        return []

    # 2. Score session value (rule-based, free)
    value = score_session_value(session_data["turns"])
    console.print(f"[dim]Session score: {value['score']} "
                  f"({value['reason']})[/dim]")

    if not value["should_extract"]:
        console.print("[yellow]Skipped: low signal[/yellow]")
        mark_session_extracted(db, session_id)
        return []

    # 3. Convert to text for LLM
    transcript_text = turns_to_conversation_text(session_data["turns"])

    # 4. Stage 1: Haiku filter
    console.print("[blue]Stage 1: Filtering (Haiku)...[/blue]")
    if dry_run:
        console.print("[dim]Dry run — skipping LLM calls[/dim]")
        return []

    filter_result = run_filter(client, transcript_text)

    # Log cost
    haiku_usage = filter_result.get("_usage", {})
    haiku_cost = _estimate_cost("haiku", haiku_usage)
    log_extraction_cost(db, session_id, "filter", "haiku",
                        haiku_usage.get("input_tokens", 0),
                        haiku_usage.get("output_tokens", 0),
                        haiku_cost)

    if not filter_result.get("has_knowledge"):
        console.print("[yellow]No knowledge candidates found[/yellow]")
        mark_session_extracted(db, session_id)
        return []

    candidates = filter_result.get("candidates", [])
    console.print(f"[green]Found {len(candidates)} candidate(s)[/green]")

    # 5. Stage 2: Sonnet structure each candidate
    existing = get_all_knowledge(db)

    for i, candidate in enumerate(candidates):
        console.print(f"[blue]Stage 2: Structuring candidate {i+1}/{len(candidates)} (Sonnet)...[/blue]")

        # Extract relevant excerpt from turns
        start, end = candidate.get("turn_range", [0, len(session_data["turns"])])
        excerpt_turns = session_data["turns"][start:end+1]
        excerpt = turns_to_conversation_text(excerpt_turns, max_tokens=3000)

        try:
            knowledge = run_structure(client, candidate, excerpt, existing)
        except (json.JSONDecodeError, anthropic.APIError) as e:
            console.print(f"[red]Structure failed: {e}[/red]")
            continue

        # Log cost
        sonnet_usage = knowledge.get("_usage", {})
        sonnet_cost = _estimate_cost("sonnet", sonnet_usage)
        log_extraction_cost(db, session_id, "structure", "sonnet",
                            sonnet_usage.get("input_tokens", 0),
                            sonnet_usage.get("output_tokens", 0),
                            sonnet_cost)

        # Clean up internal fields
        knowledge.pop("_usage", None)
        knowledge.pop("practical_insight", None)  # Log separately if needed

        # 6. Dedup check
        existing_contents = [k["content"] for k in existing]
        is_dup, sim = is_duplicate(knowledge["content"], existing_contents)

        if is_dup:
            console.print(f"[yellow]Duplicate (sim={sim}): {knowledge['title']}[/yellow]")
            continue

        # 7. Find related items
        related = find_related(knowledge["content"], existing)
        knowledge["related_ids"] = related
        knowledge["source_session_id"] = session_id

        # Auto-approve high-confidence LLM-structured knowledge
        if knowledge.get("confidence", 0) >= 0.9:
            knowledge["approved"] = 1

        # 8. Save
        save_knowledge(db, knowledge)
        extracted.append(knowledge)
        existing.append(knowledge)  # For dedup of subsequent candidates

        console.print(f"[green]✓ Extracted: {knowledge['title']} "
                      f"(confidence={knowledge.get('confidence', '?')})[/green]")

    mark_session_extracted(db, session_id)
    db.close()
    return extracted


def extract_pending(dry_run: bool = False, project_path: str | None = None):
    """Extract all pending sessions, optionally scoped to one project."""
    db = get_db()
    pending = get_pending_sessions(db, project_path=project_path)
    db.close()

    if not pending:
        scope = f" for {project_path}" if project_path else ""
        console.print(f"[dim]No pending sessions{scope}.[/dim]")
        return

    scope = f" (project: {project_path})" if project_path else ""
    console.print(
        f"[bold]Processing {len(pending)} pending session(s){scope}...[/bold]"
    )

    total_extracted = 0
    for session in pending:
        console.print(f"\n[bold]Session: {session['id'][:12]}...[/bold]")
        results = extract_session(
            session["transcript_path"],
            session["id"],
            dry_run=dry_run,
        )
        total_extracted += len(results)

    console.print(f"\n[bold green]Done. Extracted {total_extracted} knowledge item(s).[/bold green]")


def _estimate_cost(model: str, usage: dict) -> float:
    """Estimate API cost in USD."""
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)

    # Approximate pricing (per 1M tokens)
    if "haiku" in model:
        return (inp * 0.25 + out * 1.25) / 1_000_000
    elif "sonnet" in model:
        return (inp * 3.0 + out * 15.0) / 1_000_000
    else:
        return (inp * 15.0 + out * 75.0) / 1_000_000


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Mojo knowledge extraction")
    parser.add_argument("--session", help="Extract specific session by path")
    parser.add_argument("--session-id", help="Session ID (used with --session)")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    parser.add_argument(
        "--project", "-p",
        help="Only extract sessions belonging to this project path "
             "(defaults to the current working directory). Use '--project all' "
             "to process every pending session across projects.",
    )
    args = parser.parse_args()

    if args.session:
        sid = args.session_id or Path(args.session).stem
        extract_session(args.session, sid, dry_run=args.dry_run)
        return

    if args.project and args.project.lower() == "all":
        project_path: str | None = None
    else:
        project_path = args.project or str(Path.cwd())
    extract_pending(dry_run=args.dry_run, project_path=project_path)


if __name__ == "__main__":
    main()
