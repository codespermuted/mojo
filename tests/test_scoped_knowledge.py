import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db_ops
from db_ops import (
    get_db,
    init_db,
    mark_conflict,
    normalize_knowledge_item,
    save_knowledge,
    set_promotion_state,
)
from scan import scan_and_save, scan_markdown_notes


def test_schema_migration_adds_scoped_columns(tmp_path):
    db_path = tmp_path / "old.db"
    db = sqlite3.connect(db_path)
    db.execute("""
        CREATE TABLE knowledge (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            domain TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            reasoning TEXT,
            confidence REAL DEFAULT 0.5,
            source_session_id TEXT,
            related_ids TEXT DEFAULT '[]',
            related_reasoning TEXT DEFAULT '{}',
            tags TEXT DEFAULT '[]',
            usage_count INTEGER DEFAULT 0,
            last_used_at TEXT,
            approved INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            status TEXT DEFAULT 'standalone',
            parent_id TEXT,
            detail_ids TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()

    init_db(db_path)
    db = get_db(db_path)
    cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge)").fetchall()}
    db.close()

    for col in {
        "taxon", "scope", "applies_when", "does_not_apply_when",
        "evidence_level", "promotion_state", "project_path",
        "source_lineage", "evidence_excerpt", "counterexamples",
        "conflicts_with", "review_required", "safe_to_generalize",
    }:
        assert col in cols


def test_scoped_knowledge_round_trips(tmp_path):
    db_path = tmp_path / "mojo.db"
    init_db(db_path)
    db = get_db(db_path)
    save_knowledge(db, {
        "id": "scope-001",
        "type": "architecture_decision",
        "taxon": "decision_rationale",
        "domain": "web/postgres",
        "title": "Prefer separate tag table here",
        "content": "For this service, model tags as a separate table.",
        "reasoning": "The workload needs indexed aggregation.",
        "scope": "project",
        "applies_when": "When changing the service schema.",
        "does_not_apply_when": "Do not apply to prototypes without reporting needs.",
        "evidence_level": "local_rule",
        "promotion_state": "candidate",
        "project_path": str(tmp_path),
        "source_lineage": {"kind": "manual_note", "ref": "test"},
        "counterexamples": ["throwaway prototype"],
        "conflicts_with": [],
        "confidence": 0.8,
        "tags": ["postgres", "tags"],
    })
    row = db.execute("SELECT * FROM knowledge WHERE id = 'scope-001'").fetchone()
    db.close()

    assert row["scope"] == "project"
    assert json.loads(row["source_lineage"])["kind"] == "manual_note"
    assert json.loads(row["counterexamples"]) == ["throwaway prototype"]


def test_overgeneralized_strong_rule_is_downgraded():
    item = normalize_knowledge_item({
        "id": "rule-001",
        "type": "domain_rule",
        "domain": "python/datetime",
        "title": "Always use now_kst",
        "content": "Always use now_kst for timestamps.",
        "reasoning": "Observed in one project.",
        "scope": "universal",
        "evidence_level": "local_rule",
        "promotion_state": "candidate",
        "confidence": 0.8,
    })

    assert item["scope"] == "domain"
    assert item["promotion_state"] == "candidate"
    assert item["safe_to_generalize"] == 0
    assert "Do not treat this as universal" in item["does_not_apply_when"]


def test_promotion_state_transitions(tmp_path):
    db_path = tmp_path / "mojo.db"
    init_db(db_path)
    db = get_db(db_path)
    save_knowledge(db, {
        "id": "promo-001",
        "type": "domain_rule",
        "domain": "project/demo",
        "title": "Use project helper",
        "content": "Use the project helper in this repo.",
        "reasoning": "The helper owns edge cases.",
        "applies_when": "When working in this repo.",
        "confidence": 0.8,
    })
    save_knowledge(db, {
        "id": "promo-002",
        "type": "domain_rule",
        "domain": "project/demo",
        "title": "Use another helper",
        "content": "Use a conflicting helper.",
        "reasoning": "Different observed path.",
        "confidence": 0.6,
    })

    set_promotion_state(db, "promo-001", "project_approved", scope="project")
    row = db.execute("SELECT * FROM knowledge WHERE id = 'promo-001'").fetchone()
    assert row["promotion_state"] == "project_approved"
    assert row["approved"] == 1
    assert row["scope"] == "project"

    set_promotion_state(db, "promo-001", "generalized", scope="universal")
    row = db.execute("SELECT * FROM knowledge WHERE id = 'promo-001'").fetchone()
    assert row["promotion_state"] == "generalized"
    assert row["safe_to_generalize"] == 1
    assert row["evidence_level"] == "generalized_principle"

    mark_conflict(db, "promo-001", "promo-002")
    row = db.execute("SELECT conflicts_with FROM knowledge WHERE id = 'promo-001'").fetchone()
    db.close()
    assert "promo-002" in json.loads(row["conflicts_with"])


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_git_scan_preserves_raw_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("print('old')\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / "app.py").write_text("print('fixed')\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", "fix: preserve scoped raw evidence")

    scan_and_save(str(repo), max_commits=10)
    db = get_db(db_path)
    row = db.execute("SELECT * FROM knowledge WHERE source_session_id LIKE 'git-scan-%'").fetchone()
    db.close()

    assert row is not None
    assert row["promotion_state"] == "raw"
    assert row["evidence_level"] == "raw_observation"
    assert row["project_path"] == str(repo.resolve())
    assert json.loads(row["source_lineage"])["kind"] == "commit"
    assert "print('fixed')" in row["evidence_excerpt"]


def test_markdown_notes_import_as_raw_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)
    project = tmp_path / "repo"
    project.mkdir()
    (project / "NOTES.md").write_text(
        "# Auth Cache Note\n\n"
        "When debugging this service, stale auth state may come from the local dependency cache. "
        "This is raw evidence and needs review before becoming guidance.\n",
        encoding="utf-8",
    )

    scan_markdown_notes(str(project))
    db = get_db(db_path)
    row = db.execute("SELECT * FROM knowledge WHERE source_session_id LIKE 'markdown-note-%'").fetchone()
    db.close()

    assert row is not None
    assert row["promotion_state"] == "raw"
    assert row["evidence_level"] == "raw_observation"
    assert json.loads(row["source_lineage"])["kind"] == "markdown_note"
    assert row["project_path"] == str(project.resolve())
