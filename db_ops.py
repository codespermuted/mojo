"""Mojo database operations."""

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional


def get_mojo_home() -> Path:
    """Get Mojo home directory. Configurable via MOJO_HOME env var.
    
    Priority:
      1. MOJO_HOME env var (e.g., /mnt/nas/mojo, /data/mojo)
      2. Default: ~/.mojo
    """
    return Path(os.environ.get("MOJO_HOME", Path.home() / ".mojo"))


MOJO_DIR = get_mojo_home()
DB_PATH = MOJO_DIR / "mojo.db"
# Resolve symlinks so dev installs (scripts/dev-install.sh symlinks
# db_ops.py back into site-packages) still find schema.sql alongside
# the source tree copy rather than a potentially stale site-packages
# snapshot.
SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"


CONFIDENCE_GRADES = {
    "A": {"label": "Verified",     "color": "#4ECDC4",
          "description": "Multi-source confirmed or usage-validated"},
    "B": {"label": "Corroborated", "color": "#87CEEB",
          "description": "Single source with explicit reasoning, approved"},
    "C": {"label": "Reported",     "color": "#FFB347",
          "description": "Auto-extracted, single source, unverified"},
    "D": {"label": "Inferred",     "color": "#DDA0DD",
          "description": "Weak signal, ambiguous context"},
    "F": {"label": "Contested",    "color": "#FF6B6B",
          "description": "Contradicted, very low confidence, or long-unused"},
}

GRADE_ORDER = ["A", "B", "C", "D", "F"]


def evidence_based_grade(item: dict) -> str:
    """Grade based on evidence quality, not arbitrary thresholds.

    Criteria (first match wins):

    F - Contested:  confidence < 0.3, or never used for > 180 days.
    A - Verified:   >= 2 related_ids (multi-source), or (usage >= 3 AND approved).
    B - Corroborated: has non-empty reasoning AND approved.
    D - Inferred:   confidence < 0.5, or (no reasoning AND not approved).
    C - Reported:   default for auto-extracted items.
    """
    approved = bool(item.get("approved", 0))
    usage = item.get("usage_count", 0) or 0
    reasoning_text = (item.get("reasoning") or "").strip()
    has_reasoning = bool(reasoning_text)
    confidence = item.get("confidence", 0.5) or 0.0
    related = item.get("related_ids") or []
    if isinstance(related, str):
        import json as _json
        try:
            related = _json.loads(related)
        except (ValueError, TypeError):
            related = []
    has_multiple_sources = len(related) >= 2

    # F: Contested — long unused or very low confidence
    created = item.get("created_at", "")
    if created:
        try:
            age_days = (datetime.now() - datetime.fromisoformat(created)).days
            if usage == 0 and age_days > 180:
                return "F"
        except (ValueError, TypeError):
            pass
    if confidence < 0.3:
        return "F"

    # A: Verified
    if has_multiple_sources or (usage >= 3 and approved):
        return "A"

    # B: Corroborated
    if has_reasoning and approved:
        return "B"

    # D: Inferred
    if confidence < 0.5 or (not has_reasoning and not approved):
        return "D"

    # C: Reported (default)
    return "C"


def get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get database connection, creating schema if needed."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db(db_path: Optional[Path] = None):
    """Initialize database and migrate old schemas in-place."""
    db = get_db(db_path)
    schema_sql = SCHEMA_PATH.read_text()
    db.executescript(schema_sql)

    # Migration: add columns introduced after the initial schema.
    migrations = [
        ("related_reasoning", "TEXT DEFAULT '{}'"),
        ("status",            "TEXT DEFAULT 'standalone'"),
        ("parent_id",         "TEXT"),
        ("detail_ids",        "TEXT DEFAULT '[]'"),
        ("related_scores",    "TEXT DEFAULT '{}'"),
    ]
    status_added = False
    for col, ddl in migrations:
        try:
            db.execute(f"SELECT {col} FROM knowledge LIMIT 1")
        except sqlite3.OperationalError:
            db.execute(f"ALTER TABLE knowledge ADD COLUMN {col} {ddl}")
            if col == "status":
                status_added = True

    # extraction_costs migrations: cache token tracking
    cost_migrations = [
        ("cache_read_input_tokens",     "INTEGER DEFAULT 0"),
        ("cache_creation_input_tokens", "INTEGER DEFAULT 0"),
    ]
    for col, ddl in cost_migrations:
        try:
            db.execute(f"SELECT {col} FROM extraction_costs LIMIT 1")
        except sqlite3.OperationalError:
            db.execute(f"ALTER TABLE extraction_costs ADD COLUMN {col} {ddl}")

    # Backfill: reclassify git-scan rows as detail the first time the
    # column exists, OR if no detail rows exist yet at all (recovery from
    # an earlier migration that incorrectly kept them as standalone).
    should_backfill = status_added
    if not should_backfill:
        any_detail = db.execute(
            "SELECT 1 FROM knowledge WHERE status = 'detail' LIMIT 1"
        ).fetchone()
        any_git = db.execute(
            "SELECT 1 FROM knowledge WHERE source_session_id LIKE 'git-scan%' LIMIT 1"
        ).fetchone()
        if not any_detail and any_git:
            should_backfill = True

    if should_backfill:
        db.execute("""
            UPDATE knowledge
               SET status = 'detail'
             WHERE source_session_id LIKE 'git-scan%'
               AND status = 'standalone'
        """)
    db.commit()
    db.close()


def register_session(db: sqlite3.Connection, session_id: str,
                     transcript_path: str, project_path: str = ""):
    """Register a captured session for extraction."""
    db.execute("""
        INSERT OR IGNORE INTO raw_sessions (id, transcript_path, project_path)
        VALUES (?, ?, ?)
    """, (session_id, transcript_path, project_path))
    db.commit()


def save_knowledge(db: sqlite3.Connection, item: dict):
    """Save a knowledge item to the database."""
    related_reasoning = item.get("related_reasoning", {})
    if not isinstance(related_reasoning, str):
        related_reasoning = json.dumps(related_reasoning, ensure_ascii=False)
    related_scores = item.get("related_scores", {})
    if not isinstance(related_scores, str):
        related_scores = json.dumps(related_scores, ensure_ascii=False)
    db.execute("""
        INSERT OR REPLACE INTO knowledge
        (id, type, domain, title, content, reasoning, confidence,
         source_session_id, related_ids, related_reasoning, related_scores,
         tags, usage_count, approved, status, parent_id, detail_ids, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["id"], item["type"], item["domain"], item["title"],
        item["content"], item.get("reasoning", ""),
        item.get("confidence", 0.5), item.get("source_session_id", ""),
        json.dumps(item.get("related_ids", []), ensure_ascii=False),
        related_reasoning,
        related_scores,
        json.dumps(item.get("tags", []), ensure_ascii=False),
        item.get("usage_count", 0), item.get("approved", 0),
        item.get("status", "standalone"),
        item.get("parent_id"),
        json.dumps(item.get("detail_ids", []), ensure_ascii=False),
        datetime.now().isoformat()
    ))
    db.commit()


def get_knowledge_by_domain(db: sqlite3.Connection, domain_prefix: str,
                            min_confidence: float = 0.5,
                            include_archived: bool = False) -> list[dict]:
    """Get knowledge items matching a domain prefix."""
    query = """
        SELECT * FROM knowledge 
        WHERE domain LIKE ? AND confidence >= ?
    """
    if not include_archived:
        query += " AND archived = 0"
    query += " ORDER BY confidence DESC, usage_count DESC"

    rows = db.execute(query, (f"{domain_prefix}%", min_confidence)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_knowledge(db: sqlite3.Connection,
                      min_confidence: float = 0.0,
                      approved_only: bool = False) -> list[dict]:
    """Get all non-archived knowledge items."""
    query = "SELECT * FROM knowledge WHERE archived = 0 AND confidence >= ?"
    if approved_only:
        query += " AND approved = 1"
    query += " ORDER BY domain, confidence DESC"
    rows = db.execute(query, (min_confidence,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_pending_sessions(db: sqlite3.Connection,
                         project_path: str | None = None) -> list[dict]:
    """Get sessions not yet extracted.

    If ``project_path`` is given, return only sessions belonging to that
    project. A session's stored ``project_path`` can be either the project's
    absolute filesystem path (set by the SessionEnd hook from ``cwd``) or
    Claude Code's per-project directory under ``~/.claude/projects/`` using
    its encoded form (set by ``scan.py``). We match both shapes.
    """
    if project_path is None:
        rows = db.execute(
            "SELECT * FROM raw_sessions WHERE extracted = 0 ORDER BY created_at"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    abs_path = str(Path(project_path).expanduser().resolve())
    # Claude Code encodes project paths by replacing both "/" and "_"
    # with "-" (leading slash → leading dash), so
    # "/workspace/Desktop/cloud_forecasting" becomes
    # "-workspace-Desktop-cloud-forecasting". Verified empirically on
    # Claude Code 2.x; do not change without retesting a path containing
    # an underscore.
    encoded = abs_path.replace("/", "-").replace("_", "-")
    # The scan backfill stores the Claude Code per-project directory as
    # ~/.claude/projects/<encoded>. Match it exactly — never as a LIKE
    # substring, because sibling projects with prefix-shared names
    # (e.g. "mojo" and "mojo-experiment") would otherwise bleed together.
    scan_form = str(Path.home() / ".claude" / "projects" / encoded)
    rows = db.execute(
        """
        SELECT * FROM raw_sessions
        WHERE extracted = 0
          AND project_path IN (?, ?)
        ORDER BY created_at
        """,
        (abs_path, scan_form),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_session_extracted(db: sqlite3.Connection, session_id: str):
    """Mark a session as extracted."""
    db.execute(
        "UPDATE raw_sessions SET extracted = 1 WHERE id = ?", (session_id,)
    )
    db.commit()


def increment_usage(db: sqlite3.Connection, knowledge_id: str):
    """Increment usage count for a knowledge item."""
    db.execute("""
        UPDATE knowledge 
        SET usage_count = usage_count + 1, 
            last_used_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
    """, (knowledge_id,))
    db.commit()


def update_confidence(db: sqlite3.Connection, knowledge_id: str, delta: float):
    """Adjust confidence score, clamped to [0, 1]."""
    db.execute("""
        UPDATE knowledge 
        SET confidence = MAX(0.0, MIN(1.0, confidence + ?)),
            updated_at = datetime('now')
        WHERE id = ?
    """, (delta, knowledge_id))
    db.commit()


def log_extraction_cost(db: sqlite3.Connection, session_id: str,
                        stage: str, model: str,
                        input_tokens: int, output_tokens: int, cost_usd: float,
                        cache_read_input_tokens: int = 0,
                        cache_creation_input_tokens: int = 0):
    """Log API cost for an extraction step (including cache tokens)."""
    db.execute("""
        INSERT INTO extraction_costs (
            session_id, stage, model,
            input_tokens, output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens,
            cost_usd
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (session_id, stage, model,
          input_tokens, output_tokens,
          cache_read_input_tokens, cache_creation_input_tokens,
          cost_usd))
    db.commit()


def get_stats(db: sqlite3.Connection) -> dict:
    """Get overall statistics."""
    total = db.execute("SELECT COUNT(*) FROM knowledge WHERE archived=0").fetchone()[0]
    by_type = db.execute(
        "SELECT type, COUNT(*) as cnt FROM knowledge WHERE archived=0 GROUP BY type"
    ).fetchall()
    by_domain = db.execute(
        "SELECT domain, COUNT(*) as cnt FROM knowledge WHERE archived=0 GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()
    total_cost = db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM extraction_costs"
    ).fetchone()[0]
    total_usage = db.execute(
        "SELECT COALESCE(SUM(usage_count), 0) FROM knowledge"
    ).fetchone()[0]

    return {
        "total_knowledge": total,
        "by_type": {r["type"]: r["cnt"] for r in by_type},
        "by_domain": {r["domain"]: r["cnt"] for r in by_domain},
        "total_extraction_cost_usd": round(total_cost, 4),
        "total_usage_count": total_usage,
    }


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("related_ids", "tags", "detail_ids"):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    for obj_field in ("related_reasoning", "related_scores"):
        if obj_field in d and isinstance(d[obj_field], str):
            try:
                d[obj_field] = json.loads(d[obj_field])
            except (json.JSONDecodeError, TypeError):
                d[obj_field] = {}
    return d


def get_summaries(db: sqlite3.Connection,
                  min_confidence: float = 0.0,
                  approved_only: bool = False) -> list[dict]:
    """Fetch top-layer items (summary + standalone) for CLAUDE.md injection."""
    query = ("SELECT * FROM knowledge WHERE archived = 0 "
             "AND status IN ('summary', 'standalone') "
             "AND confidence >= ?")
    if approved_only:
        query += " AND approved = 1"
    query += " ORDER BY domain, confidence DESC"
    rows = db.execute(query, (min_confidence,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_details_for(db: sqlite3.Connection, parent_id: str) -> list[dict]:
    """Fetch details that belong to a given summary."""
    rows = db.execute(
        "SELECT * FROM knowledge WHERE parent_id = ? AND archived = 0 "
        "ORDER BY created_at",
        (parent_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_orphan_details(db: sqlite3.Connection) -> list[dict]:
    """Fetch detail rows that are not yet linked to any summary."""
    rows = db.execute(
        "SELECT * FROM knowledge WHERE status = 'detail' "
        "AND (parent_id IS NULL OR parent_id = '') AND archived = 0 "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def link_detail_to_summary(db: sqlite3.Connection,
                           detail_id: str, summary_id: str):
    """Attach a detail to a summary, updating both sides of the link."""
    db.execute(
        "UPDATE knowledge SET parent_id = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (summary_id, detail_id),
    )
    row = db.execute(
        "SELECT detail_ids FROM knowledge WHERE id = ?", (summary_id,)
    ).fetchone()
    if row:
        try:
            ids = json.loads(row[0] or "[]")
        except (json.JSONDecodeError, TypeError):
            ids = []
        if detail_id not in ids:
            ids.append(detail_id)
            db.execute(
                "UPDATE knowledge SET detail_ids = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (json.dumps(ids, ensure_ascii=False), summary_id),
            )
    db.commit()
