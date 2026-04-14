#!/usr/bin/env python3
"""Initialize Mojo: create DB, register hooks, copy default config."""

import json
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent))

from db_ops import init_db, MOJO_DIR

console = Console()

# Resolve symlinks before deriving sibling directories. Editable
# dev installs symlink init.py from site-packages back to the source
# tree (see scripts/dev-install.sh), and ``Path(__file__).parent``
# on a symlinked module returns the *link's* parent (site-packages),
# not the source-tree parent where the hooks/ and config/ siblings
# actually live. Calling .resolve() once here fixes the hook-copy
# step for both regular and dev installs.
_SOURCE_ROOT = Path(__file__).resolve().parent
HOOKS_DIR = _SOURCE_ROOT / "hooks"
CONFIG_DIR = _SOURCE_ROOT / "config"


def init_mojo(skip_hooks: bool = False):
    """Full Mojo initialization."""
    console.print(Panel.fit(
        "[bold]Mojo — Knowledge Distillation for Claude Code[/bold]\n"
        "Initializing...",
        border_style="blue"
    ))

    # Upfront: be explicit about which MOJO_HOME we resolved and
    # whether the user is running a per-project sidecar or a global
    # store. This is the single most load-bearing piece of info for
    # anyone debugging "where did my knowledge go?".
    default_home = Path.home() / ".mojo"
    if MOJO_DIR.resolve() == default_home.resolve():
        scope_label = "[bold]global[/bold] (default)"
    else:
        scope_label = "[bold yellow]per-project sidecar[/bold yellow]"
    console.print(
        f"[dim]MOJO_HOME = {MOJO_DIR} — {scope_label}[/dim]"
    )

    # 1. Create ~/.mojo directory
    MOJO_DIR.mkdir(parents=True, exist_ok=True)
    (MOJO_DIR / "skills").mkdir(exist_ok=True)
    console.print(f"[green]✓[/green] Created {MOJO_DIR}")

    # 2. Initialize SQLite database
    init_db()
    console.print(f"[green]✓[/green] Database initialized")

    # 3. Copy default config if not exists
    config_dest = MOJO_DIR / "config.yaml"
    if not config_dest.exists():
        config_src = CONFIG_DIR / "default.yaml"
        if config_src.exists():
            shutil.copy2(config_src, config_dest)
            console.print(f"[green]✓[/green] Config: {config_dest}")
        else:
            console.print("[yellow]⚠ Default config not found, skipping[/yellow]")
    else:
        console.print(f"[dim]  Config already exists: {config_dest}[/dim]")

    # 4. Copy hook scripts
    hooks_dest = MOJO_DIR / "hooks"
    hooks_dest.mkdir(exist_ok=True)
    for hook_file in HOOKS_DIR.glob("*.py"):
        dest = hooks_dest / hook_file.name
        shutil.copy2(hook_file, dest)
        dest.chmod(0o755)
    console.print(f"[green]✓[/green] Hook scripts: {hooks_dest}")

    # 5. Register hooks in Claude Code settings
    if not skip_hooks:
        registered, settings_path, replaced = register_claude_hooks()
        if registered:
            note = " (replaced stale entry)" if replaced else ""
            console.print(
                f"[green]✓[/green] Claude Code hooks registered in "
                f"[dim]{settings_path}[/dim]{note}"
            )
            # With runtime cwd-based resolution (hooks/_resolve.py) a
            # per-project sidecar no longer hijacks sessions from
            # unrelated projects. Reassure the user on the sidecar
            # path instead of warning.
            if MOJO_DIR.resolve() != default_home.resolve():
                console.print(
                    "[dim]  Per-project sidecar: the hook script auto-detects "
                    "`.mojo/` by walking up from\n  the session's cwd, so "
                    "sessions in other projects fall back to the global "
                    "store.[/dim]"
                )
        else:
            console.print(
                "[yellow]⚠ Could not auto-register hooks. Add manually:[/yellow]\n"
                f"  See instructions below."
            )
    else:
        console.print("[dim]  Skipped hook registration (--skip-hooks)[/dim]")

    # 6. Done
    console.print()
    console.print(Panel.fit(
        "[bold green]Mojo initialized![/bold green]\n\n"
        "Next steps:\n"
        "  1. Open the dashboard:     [cyan]mojo dashboard[/cyan]\n"
        "  2. Seed a project (free):  [cyan]mojo scan git /path/to/project[/cyan]\n"
        "  3. Sync into CLAUDE.md:    [cyan]mojo sync --project /path/to/project[/cyan]\n"
        "  4. Review / approve items: [cyan]mojo review[/cyan]\n\n"
        "Hooks run automatically on the next Claude Code session — no further setup needed.",
        border_style="green"
    ))


def register_claude_hooks() -> tuple[bool, Path, bool]:
    """Register Mojo hooks in Claude Code's global settings.json.

    Returns ``(registered, settings_path, replaced)``. ``replaced`` is
    True when a previous mojo entry was found and overwritten — useful
    for telling the user that an older install's hook path was cleaned
    up.

    The registered command deliberately does **not** bake a
    ``MOJO_HOME=...`` prefix into the shell command any more. The hook
    script itself resolves the target ``mojo.db`` at runtime from the
    session's ``cwd`` (see ``hooks/_resolve.py``), so one global
    registration cleanly covers both the default store and any
    per-project ``.mojo`` sidecars without cross-project bleed.
    """
    settings_path = Path.home() / ".claude" / "settings.json"

    if not settings_path.parent.exists():
        console.print("[dim]  ~/.claude not found — Claude Code not installed?[/dim]")
        return False, settings_path, False

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    hooks = settings.get("hooks", {})
    hooks_base = str(MOJO_DIR / "hooks")

    replaced = _replace_mojo_entry(
        hooks, event="SessionEnd",
        script_name="on_session_end.py", hooks_base=hooks_base,
    )
    replaced |= _replace_mojo_entry(
        hooks, event="Stop",
        script_name="on_stop.py", hooks_base=hooks_base,
    )

    settings["hooks"] = hooks

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True, settings_path, replaced


def _replace_mojo_entry(hooks: dict, *, event: str, script_name: str,
                        hooks_base: str) -> bool:
    """Ensure exactly one mojo entry exists for ``event``.

    Any pre-existing mojo entry (identified by the presence of
    ``mojo`` or the hook's script name in its JSON form) is stripped
    first. This prevents a re-init from a different ``MOJO_HOME`` —
    or an old install that baked ``MOJO_HOME=...`` into the command
    string — from leaving behind a stale hook path in settings.json.
    Returns True iff a prior entry was found and dropped.
    """
    event_list = hooks.get(event, [])
    filtered = []
    dropped = False
    for entry in event_list:
        blob = json.dumps(entry).lower()
        if script_name in blob or "mojo" in blob:
            dropped = True
            continue
        filtered.append(entry)

    filtered.append({
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {hooks_base}/{script_name}",
        }],
    })
    hooks[event] = filtered
    return dropped


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Initialize Mojo")
    parser.add_argument("--skip-hooks", action="store_true",
                        help="Skip Claude Code hook registration")
    args = parser.parse_args()

    init_mojo(skip_hooks=args.skip_hooks)


if __name__ == "__main__":
    main()
