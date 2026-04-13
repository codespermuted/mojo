#!/usr/bin/env python3
"""Hook: Stop — Quick correction signal scan on session end.

Runs after Claude finishes responding. Scans the transcript for
correction signals and marks the session if high-value signals found.
Lightweight: rule-based only, no LLM calls.
"""

import json
import os
import sys
import sqlite3
from pathlib import Path

MOJO_HOME = Path(os.environ.get("MOJO_HOME", Path.home() / ".mojo"))
MOJO_DB = MOJO_HOME / "mojo.db"

# Quick regex-free keyword scan for speed
CORRECTION_KEYWORDS = [
    "아니야", "아니요", "그게 아니라", "잘못", "틀렸",
    "실제로는", "현실에서는", "우리 도메인",
    "wrong", "actually", "instead", "don't use", "never use",
    "should be", "not correct",
]


def main():
    try:
        hook_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    session_id = hook_data.get("session_id", "")
    transcript_path = hook_data.get("transcript_path", "")

    if not session_id or not transcript_path or not MOJO_DB.exists():
        return

    # Quick scan: read last N lines of transcript for correction keywords
    try:
        path = Path(transcript_path)
        if not path.exists():
            return

        # Read only user messages from last 2000 chars (fast scan)
        content = path.read_text(encoding="utf-8", errors="ignore")[-5000:]
        has_corrections = any(kw in content for kw in CORRECTION_KEYWORDS)

        if has_corrections:
            db = sqlite3.connect(str(MOJO_DB))
            db.execute(
                "UPDATE raw_sessions SET has_corrections = 1 WHERE id = ?",
                (session_id,)
            )
            db.commit()
            db.close()
    except (OSError, sqlite3.Error):
        pass  # Silent fail


if __name__ == "__main__":
    main()
