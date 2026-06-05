"""Shared MOJO_HOME resolution for Claude Code hook scripts.

The hook resolves which ``mojo.db`` to write into in this order:

1. ``$MOJO_HOME`` env var if set — explicit user override, preserved
   mainly for tests and advanced pinning.
2. Walk up from the hook payload's ``cwd`` looking for a ``.mojo``
   directory. First match wins. This is what enables "just works"
   per-project sidecars: running ``mojo init`` once globally, then
   dropping a ``.mojo`` directory inside any project, automatically
   scopes that project's session capture to the sidecar DB.
3. Fallback to ``~/.mojo`` — the global default store.

Historically the hook command registered in ``~/.claude/settings.json``
had ``MOJO_HOME=<absolute-path>`` baked in at ``mojo init`` time, so
every Claude Code session on the machine — regardless of cwd —
silently pushed transcripts into whichever project last ran init.
This module is the runtime replacement that makes that prefix
unnecessary.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# Set by extract/llm_backend.py in every CLI child process. A headless
# `claude -p` spawned by the extraction pipeline fires these same hooks;
# without this guard an extraction would register itself as a session and
# trigger another extraction, recursively.
EXTRACTION_ENV_FLAG = "MOJO_EXTRACTION"


def is_extraction_context() -> bool:
    """True when this hook fired inside a Mojo extraction subprocess."""
    return bool(os.environ.get(EXTRACTION_ENV_FLAG))


def auto_extract_enabled(mojo_home: Path) -> bool:
    """Read extraction.auto_extract from config.yaml without PyYAML.

    Hooks run under system python3 with no package environment, so this
    is a deliberately dumb regex read. Defaults to True (matching the
    packaged default config) when the file or key is missing.
    """
    config_path = mojo_home / "config.yaml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return True
    m = re.search(r"^\s*auto_extract:\s*(\S+)", text, re.MULTILINE)
    if not m:
        return True
    return m.group(1).strip().lower() not in ("false", "no", "0", "off")


def resolve_mojo_db(cwd: Optional[str]) -> Optional[Path]:
    """Return the ``mojo.db`` path that should receive this hook event.

    Returns ``None`` when no initialized store exists anywhere on the
    resolution chain — the caller should bail silently in that case
    (``mojo init`` has not been run, so there is nothing to record).
    """
    env_home = os.environ.get("MOJO_HOME")
    if env_home:
        candidate = Path(env_home).expanduser() / "mojo.db"
        return candidate if candidate.exists() else None

    if cwd:
        cwd_path = Path(cwd).expanduser()
        try:
            cwd_path = cwd_path.resolve()
        except (OSError, RuntimeError):
            pass
        # Walk upward. First ``.mojo/mojo.db`` wins — a project-local
        # sidecar always overrides the global store.
        for parent in [cwd_path, *cwd_path.parents]:
            candidate = parent / ".mojo" / "mojo.db"
            if candidate.exists():
                return candidate

    global_db = Path.home() / ".mojo" / "mojo.db"
    return global_db if global_db.exists() else None
