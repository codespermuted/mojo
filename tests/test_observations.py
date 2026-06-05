import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import db_ops
from db_ops import (
    get_db, get_observations, init_db, observation_summary,
    record_observation, save_knowledge,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)
    init_db(db_path)
    conn = get_db(db_path)
    yield conn
    conn.close()


def _item(item_id="ts-001"):
    return {
        "id": item_id,
        "type": "domain_rule",
        "domain": "ml/timeseries",
        "title": "Walk-forward only",
        "content": "Time-series validation must be walk-forward, never shuffled.",
        "reasoning": "Random splits leak future information.",
        "confidence": 0.9,
        "scope": "project",
        "project_path": "/home/u/proj-a",
    }


def test_record_and_summarize_observations(db):
    save_knowledge(db, _item())
    record_observation(db, "ts-001", project_path="/home/u/proj-a",
                       session_id="s1", stance="supports", note="initial")

    obs = get_observations(db, "ts-001")
    assert len(obs) == 1
    assert obs[0]["stance"] == "supports"

    summary = observation_summary(db, "ts-001")
    assert summary["supporting_projects"] == ["/home/u/proj-a"]
    assert summary["refuting_projects"] == []


def test_single_project_does_not_suggest_generalization(db):
    save_knowledge(db, _item())
    record_observation(db, "ts-001", project_path="/home/u/proj-a", stance="supports")
    record_observation(db, "ts-001", project_path="/home/u/proj-a", stance="supports")

    row = db.execute(
        "SELECT generalization_suggested FROM knowledge WHERE id='ts-001'"
    ).fetchone()
    assert row[0] == 0  # same project twice is not cross-project evidence


def test_two_projects_suggest_generalization(db):
    save_knowledge(db, _item())
    record_observation(db, "ts-001", project_path="/home/u/proj-a", stance="supports")
    record_observation(db, "ts-001", project_path="/home/u/proj-b", stance="supports")

    row = db.execute(
        "SELECT generalization_suggested FROM knowledge WHERE id='ts-001'"
    ).fetchone()
    assert row[0] == 1


def test_refutation_clears_suggestion(db):
    save_knowledge(db, _item())
    record_observation(db, "ts-001", project_path="/home/u/proj-a", stance="supports")
    record_observation(db, "ts-001", project_path="/home/u/proj-b", stance="supports")
    # a counterexample appears in a third project
    record_observation(db, "ts-001", project_path="/home/u/proj-c",
                       stance="refutes", note="seasonal block holdout is fine here")

    row = db.execute(
        "SELECT generalization_suggested FROM knowledge WHERE id='ts-001'"
    ).fetchone()
    assert row[0] == 0  # refuted → sharpen boundary, don't generalize


def test_invalid_stance_rejected(db):
    save_knowledge(db, _item())
    with pytest.raises(ValueError):
        record_observation(db, "ts-001", stance="maybe")


def test_grade_a_requires_cross_project_evidence(db):
    """Same-session TF-IDF neighbors must not inflate the grade to A."""
    item = _item()
    item["related_ids"] = ["x-001", "x-002"]  # similar items, same session
    save_knowledge(db, item)
    row = db.execute("SELECT * FROM knowledge WHERE id='ts-001'").fetchone()
    assert db_ops.evidence_based_grade(db_ops._row_to_dict(row)) != "A"

    # cross-project observations are real multi-source evidence
    record_observation(db, "ts-001", project_path="/home/u/proj-a", stance="supports")
    record_observation(db, "ts-001", project_path="/home/u/proj-b", stance="supports")
    row = db.execute("SELECT * FROM knowledge WHERE id='ts-001'").fetchone()
    assert db_ops.evidence_based_grade(db_ops._row_to_dict(row)) == "A"
