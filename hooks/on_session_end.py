#!/usr/bin/env python3
"""Hook: SessionEnd — Register session, then auto-extract in background.

Called by Claude Code via hooks configuration. Reads JSON from stdin.
Two steps:

1. Register the session in SQLite (lightweight, always).
2. If ``extraction.auto_extract`` is enabled, spawn a fully detached
   ``mojo extract --session ...`` so knowledge lands in the store (and
   the Obsidian vault) without any manual step. The default backend is
   headless ``claude -p`` — zero API cost — so auto-extraction is safe
   to leave on.

The target ``mojo.db`` is resolved at runtime from the payload's
``cwd`` via ``_resolve.resolve_mojo_db``, so a single global hook
registration transparently routes per-project sessions to their
sidecar stores when a ``.mojo`` directory exists alongside them.

Recursion guard: extraction subprocesses export MOJO_EXTRACTION=1; when
this hook fires inside one (headless claude sessions fire hooks too), it
bails immediately.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _resolve import auto_extract_enabled, is_extraction_context, resolve_mojo_db


def _spawn_auto_extract(mojo_db: Path, session_id: str,
                        transcript_path: str) -> None:
    """Launch a detached background extraction for this session.

    Must never block or fail the hook: the spawned process is fully
    detached (new session, output to a log file) and any error here is
    swallowed.
    """
    mojo_bin = shutil.which("mojo")
    if mojo_bin is None:
        return  # mojo not on PATH for hook env — manual `mojo extract` still works

    mojo_home = mojo_db.parent
    log_dir = mojo_home / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / "auto-extract.log").open("a")
        log_file.write(
            f"\n--- {datetime.now().isoformat()} session={session_id} ---\n")
        log_file.flush()
        env = dict(os.environ)
        env["MOJO_HOME"] = str(mojo_home)
        subprocess.Popen(
            [mojo_bin, "extract",
             "--session", transcript_path,
             "--session-id", session_id],
            stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach: survive hook process exit
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # Silent fail — hooks should never block Claude Code


def main():
    if is_extraction_context():
        return  # fired by our own headless extraction — ignore

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
        return  # Silent fail — hooks should never block Claude Code

    if auto_extract_enabled(mojo_db.parent):
        _spawn_auto_extract(mojo_db, session_id, transcript_path)


if __name__ == "__main__":
    main()
