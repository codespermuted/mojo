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

HOOKS_DIR = Path(__file__).parent / "hooks"
CONFIG_DIR = Path(__file__).parent / "config"


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
        registered, settings_path = register_claude_hooks()
        if registered:
            console.print(
                f"[green]✓[/green] Claude Code hooks registered in "
                f"[dim]{settings_path}[/dim]"
            )
            # Warn loudly when a per-project sidecar ends up writing
            # its `MOJO_HOME=...` into the *global* settings.json —
            # that means every Claude Code session on this machine,
            # regardless of cwd, will try to push transcripts into
            # this project's store. It's almost never what the user
            # wants with `MOJO_HOME=./.mojo mojo init`.
            if MOJO_DIR.resolve() != default_home.resolve():
                console.print(
                    "[yellow]  ⚠ Per-project MOJO_HOME was hard-coded "
                    "into the global settings.json above.[/yellow]\n"
                    "[dim]    Every Claude Code session on this machine "
                    "will now write to this sidecar store until you\n"
                    "    re-run `mojo init` from a different project or "
                    "remove the hook entry manually.[/dim]"
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


def register_claude_hooks() -> tuple[bool, Path]:
    """Register Mojo hooks in Claude Code's global settings.json.

    Returns ``(registered, settings_path)`` so the caller can tell the
    user exactly which file it touched. Always the *global*
    ``~/.claude/settings.json`` today — see the caller's warning for
    the per-project sidecar gotcha this creates.
    """
    # Claude Code global settings
    settings_path = Path.home() / ".claude" / "settings.json"

    if not settings_path.parent.exists():
        console.print("[dim]  ~/.claude not found — Claude Code not installed?[/dim]")
        return False, settings_path

    # Load existing settings
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    # Add hooks (preserve existing)
    hooks = settings.get("hooks", {})

    hooks_base = str(MOJO_DIR / "hooks")

    # If MOJO_HOME is custom, prepend env var to hook commands
    mojo_home_str = str(MOJO_DIR)
    default_home = str(Path.home() / ".mojo")
    env_prefix = f"MOJO_HOME={mojo_home_str} " if mojo_home_str != default_home else ""

    # SessionEnd hook
    session_end_hooks = hooks.get("SessionEnd", [])
    mojo_session_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"{env_prefix}python3 {hooks_base}/on_session_end.py"
        }]
    }

    # Check if already registered
    already_has_session = any(
        "mojo" in json.dumps(h).lower() or "on_session_end" in json.dumps(h)
        for h in session_end_hooks
    )
    if not already_has_session:
        session_end_hooks.append(mojo_session_hook)
    hooks["SessionEnd"] = session_end_hooks

    # Stop hook
    stop_hooks = hooks.get("Stop", [])
    mojo_stop_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"{env_prefix}python3 {hooks_base}/on_stop.py"
        }]
    }
    already_has_stop = any(
        "mojo" in json.dumps(h).lower() or "on_stop" in json.dumps(h)
        for h in stop_hooks
    )
    if not already_has_stop:
        stop_hooks.append(mojo_stop_hook)
    hooks["Stop"] = stop_hooks

    settings["hooks"] = hooks

    # Write back
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    return True, settings_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Initialize Mojo")
    parser.add_argument("--skip-hooks", action="store_true",
                        help="Skip Claude Code hook registration")
    args = parser.parse_args()

    init_mojo(skip_hooks=args.skip_hooks)


if __name__ == "__main__":
    main()
