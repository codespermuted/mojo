"""Tests for the intent x subject classification facets."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db_ops  # noqa: E402


def _fresh_db(tmp_path):
    p = tmp_path / "facets.db"
    db_ops.init_db(p)
    return db_ops.get_db(p)


def test_migration_adds_facet_columns(tmp_path):
    db = _fresh_db(tmp_path)
    cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge)")}
    assert "intent" in cols
    assert "subject" in cols


def test_default_facets_mapping():
    # legacy type -> intent (warning-style + rule-style both fold to constraint)
    assert db_ops.default_facets("anti_pattern", "leakage_warning") == ("constraint", "data")
    assert db_ops.default_facets("domain_rule", "environment_constraint") == ("constraint", "data")
    assert db_ops.default_facets("architecture_decision", "decision_rationale") == ("decision", "model")
    assert db_ops.default_facets("debug_playbook", "debugging_pattern") == ("playbook", "model")
    assert db_ops.default_facets("tool_preference", "tool_usage_rule") == ("preference", "external")
    # unknown values fall back safely, never crash
    assert db_ops.default_facets("???", "???") == ("constraint", "model")


def test_save_respects_explicit_facets(tmp_path):
    db = _fresh_db(tmp_path)
    db_ops.save_knowledge(db, {
        "id": "fx-1", "type": "domain_rule", "taxon": "leakage_warning",
        "domain": "electricity/smp", "title": "t", "content": "c",
        "intent": "constraint", "subject": "data",
    })
    r = db.execute("SELECT intent, subject FROM knowledge WHERE id='fx-1'").fetchone()
    assert (r["intent"], r["subject"]) == ("constraint", "data")


def test_save_fills_facets_from_legacy_when_missing(tmp_path):
    db = _fresh_db(tmp_path)
    db_ops.save_knowledge(db, {
        "id": "fx-2", "type": "debug_playbook", "taxon": "debugging_pattern",
        "domain": "ml/x", "title": "t", "content": "c",  # no intent/subject
    })
    r = db.execute("SELECT intent, subject FROM knowledge WHERE id='fx-2'").fetchone()
    assert (r["intent"], r["subject"]) == ("playbook", "model")


def test_link_detail_sets_detail_status(tmp_path):
    # structure relies on this: linking a standalone card must flip it to
    # status='detail' (else it never collapses under the summary in the UI).
    db = _fresh_db(tmp_path)
    db_ops.save_knowledge(db, {
        "id": "sum-x", "type": "domain_rule", "domain": "d", "title": "t",
        "content": "c", "status": "summary", "detail_ids": [],
    })
    db_ops.save_knowledge(db, {
        "id": "d-1", "type": "domain_rule", "domain": "d", "title": "t", "content": "c",
    })
    db_ops.link_detail_to_summary(db, "d-1", "sum-x")
    r = db.execute("SELECT status, parent_id FROM knowledge WHERE id='d-1'").fetchone()
    assert r["status"] == "detail"
    assert r["parent_id"] == "sum-x"
    s = db.execute("SELECT detail_ids FROM knowledge WHERE id='sum-x'").fetchone()
    assert "d-1" in s["detail_ids"]


def test_backfill_seed_is_valid():
    seed = json.loads((ROOT / "seeds" / "facets_backfill.json").read_text())
    assert len(seed) == 80
    for cid, f in seed.items():
        assert f["intent"] in db_ops.VALID_INTENTS, (cid, f)
        assert f["subject"] in db_ops.VALID_SUBJECTS, (cid, f)
