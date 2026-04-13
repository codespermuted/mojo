#!/usr/bin/env python3
"""Unified Mojo CLI.

Dispatches to the underlying module's main() so every subcommand keeps
its existing argparse interface. Run `mojo <command> --help` for details.
"""

from __future__ import annotations

import sys
from importlib import import_module

COMMANDS: dict[str, tuple[str, str, str]] = {
    # name           (module,              fn,     one-line help)
    "init":         ("init",              "main", "Create ~/.mojo, copy config, register Claude Code hooks"),
    "dashboard":    ("dashboard.server",  "main", "Run the web dashboard (http://localhost:8765)"),
    "scan":         ("scan",              "main", "Rule-based git / folder / sessions scan (free)"),
    "extract":      ("extract.pipeline",  "main", "Run the LLM extraction pipeline (Haiku -> Sonnet)"),
    "sync":         ("serve.sync",        "main", "Write CLAUDE.md / SKILL.md into a project"),
    "review":       ("review",            "main", "Approve / edit extracted items from the terminal"),
    "search":       ("search",            "main", "Full-text search across the knowledge store"),
    "stats":        ("stats",             "main", "Show store statistics and extraction cost"),
    "import-seed":  ("import_seed",       "main", "Bulk-import a seed knowledge JSON file"),
}

ALIASES: dict[str, str] = {
    "serve": "dashboard",  # intuitive alternative
    "ui":    "dashboard",
}


def _print_usage() -> None:
    print("mojo — knowledge distillation for Claude Code")
    print()
    print("Usage:")
    print("    mojo <command> [options]")
    print()
    print("Commands:")
    width = max(len(n) for n in COMMANDS)
    for name, (_, _, doc) in COMMANDS.items():
        print(f"    {name:<{width}}  {doc}")
    print()
    print("Run 'mojo <command> --help' for command-specific options.")
    print("Run 'mojo --version' to print the installed version.")


def _print_version() -> None:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            print(version("mojo-extract"))
            return
        except PackageNotFoundError:
            pass
    except ImportError:
        pass
    print("0.1.0")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        _print_usage()
        return 0
    cmd = sys.argv[1]
    if cmd in ("-V", "--version", "version"):
        _print_version()
        return 0
    cmd = ALIASES.get(cmd, cmd)
    if cmd not in COMMANDS:
        print(f"mojo: unknown command '{sys.argv[1]}'\n", file=sys.stderr)
        _print_usage()
        return 2

    module_name, fn_name, _ = COMMANDS[cmd]
    # Shift argv so the subcommand's argparse sees a clean argv[0] and
    # its own flags starting at argv[1:].
    sys.argv = [f"mojo {cmd}"] + sys.argv[2:]
    mod = import_module(module_name)
    fn = getattr(mod, fn_name)
    result = fn()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
