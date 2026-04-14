#!/usr/bin/env python3
"""Hook: SessionEnd — Register session for knowledge extraction.

Called by Claude Code via hooks configuration. Reads JSON from stdin.
Lightweight: only registers the session in SQLite. No LLM calls.

The target ``mojo.db`` is resolved at runtime from the payload's
``cwd`` via ``_resolve.resolve_mojo_db``, so a single global hook
registration transparently routes per-project sessions to their
sidecar stores when a ``.mojo`` directory exists alongside them.
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _resolve import resolve_mojo_db


def main():
    try:
        hook_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    session_id = hook_data.get("session_id", "")
    transcript_path = hook_data.get("transcript_path", "")
    cwd = hook_data.get("cwd", "")

    if not session_id or not transcript_path:
        return

    mojo_db = resolve_mojo_db(cwd)
    if mojo_db is None:
        return  # no initialized store on the resolution chain

    try:
        db = sqlite3.connect(str(mojo_db))
        db.execute("""
            INSERT OR IGNORE INTO raw_sessions
            (id, transcript_path, project_path, ended_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, transcript_path, cwd, datetime.now().isoformat()))
        db.commit()
        db.close()
    except sqlite3.Error:
        pass  # Silent fail — hooks should never block Claude Code


if __name__ == "__main__":
    main()
