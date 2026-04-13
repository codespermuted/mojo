#!/usr/bin/env python3
"""Hook: SessionEnd — Register session for knowledge extraction.

Called by Claude Code via hooks configuration. Reads JSON from stdin.
Lightweight: only registers the session in SQLite. No LLM calls.
"""

import json
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

MOJO_HOME = Path(os.environ.get("MOJO_HOME", Path.home() / ".mojo"))
MOJO_DB = MOJO_HOME / "mojo.db"


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

    # Ensure DB exists
    if not MOJO_DB.exists():
        return  # mojo init not yet run

    try:
        db = sqlite3.connect(str(MOJO_DB))
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
