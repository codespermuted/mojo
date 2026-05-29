import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import advisory
import companion
import db_ops
from companion import (
    classify_intervention,
    collect_context,
    log_intervention,
    run_check,
    start_companion,
    stop_companion,
    companion_status,
)
from db_ops import (
    get_companion_intervention,
    get_db,
    init_db,
    record_companion_feedback,
    save_knowledge,
    set_promotion_state,
)


def _use_temp_db(monkeypatch, db_path: Path):
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)
    monkeypatch.setattr(advisory, "DB_PATH", db_path)


def _item(project: Path, **overrides):
    payload = {
        "id": "cmp-001",
        "type": "domain_rule",
        "taxon": "implementation_pattern",
        "domain": "project/demo",
        "title": "Use local helper",
        "content": "Use the local helper in this project.",
        "reasoning": "It owns project defaults.",
        "scope": "project",
        "applies_when": "When editing helper-backed code.",
        "does_not_apply_when": "Outside this project.",
        "evidence_level": "local_rule",
        "promotion_state": "candidate",
        "project_path": str(project),
        "confidence": 0.75,
        "tags": ["helper", "project"],
    }
    payload.update(overrides)
    return payload


def test_silent_by_default(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    context = collect_context(project, task="format code")
    intervention = classify_intervention(context, [])
    assert intervention.intervention_type == "silent"


def test_hard_warning_for_instruction_file_auto_modification(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    context = collect_context(
        project,
        task="auto modify AGENTS.md and CLAUDE.md from extracted knowledge",
    )
    intervention = classify_intervention(context, [])
    assert intervention.intervention_type == "hard_warning"
    assert "explicit approval" in intervention.message


def test_project_approved_risky_knowledge_can_hard_warn(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    item = _item(
        project,
        id="risk-001",
        type="anti_pattern",
        taxon="anti_pattern",
        title="Do not edit production data",
        content="Do not run destructive cleanup against production-like data.",
        applies_when="When a command may delete production-like data.",
        confidence=0.9,
        approved=1,
        promotion_state="project_approved",
    )
    context = collect_context(project, task="delete production records during cleanup")
    intervention = classify_intervention(context, [item])
    assert intervention.intervention_type == "hard_warning"
    assert intervention.source_knowledge_ids == ["risk-001"]


def test_candidate_knowledge_soft_suggestion(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    context = collect_context(project, task="edit helper-backed code")
    intervention = classify_intervention(context, [_item(project)])
    assert intervention.intervention_type == "soft_suggestion"


def test_conflicting_knowledge_asks_clarifying_question(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    item = _item(project, conflicts_with=["cmp-009"])
    context = collect_context(project, task="edit helper-backed code")
    intervention = classify_intervention(context, [item])
    assert intervention.intervention_type == "clarifying_question"
    assert "cmp-009" in intervention.source_knowledge_ids


def test_scope_mismatch_stays_silent(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    item = _item(
        other,
        id="other-001",
        type="anti_pattern",
        taxon="anti_pattern",
        title="Do not delete records",
        confidence=0.9,
        approved=1,
        promotion_state="project_approved",
    )
    context = collect_context(project, task="delete records")
    intervention = classify_intervention(context, [item])
    assert intervention.intervention_type == "silent"


def test_raw_evidence_cannot_trigger_hard_warning_alone(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    item = _item(
        project,
        id="raw-001",
        type="anti_pattern",
        taxon="anti_pattern",
        evidence_level="raw_observation",
        promotion_state="raw",
        confidence=0.95,
    )
    context = collect_context(project, task="delete production records")
    intervention = classify_intervention(context, [item])
    assert intervention.intervention_type != "hard_warning"


def test_feedback_logging(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "repo"
    project.mkdir()
    context = collect_context(project, task="auto modify AGENTS.md")
    intervention = classify_intervention(context, [])
    logged = log_intervention(context, intervention)
    assert logged.event_id

    db = get_db(db_path)
    try:
        assert record_companion_feedback(db, logged.event_id, "too_noisy")
        row = get_companion_intervention(db, logged.event_id)
    finally:
        db.close()
    assert row["user_feedback"] == "too_noisy"
    assert row["noisy"] == 1


def test_check_does_not_mutate_instruction_files(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "repo"
    project.mkdir()
    originals = {}
    for name in ("AGENTS.md", "CLAUDE.md", "SKILLS.md"):
        path = project / name
        path.write_text(f"human {name}\n", encoding="utf-8")
        originals[name] = path.read_text(encoding="utf-8")

    intervention = run_check(
        project,
        task="auto modify AGENTS.md and CLAUDE.md",
        notify=False,
    )

    assert intervention.intervention_type == "hard_warning"
    for name, content in originals.items():
        assert (project / name).read_text(encoding="utf-8") == content


def test_sidecar_state_start_status_stop(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path / "mojo.db")
    project = tmp_path / "repo"
    project.mkdir()
    state = start_companion(project, start_process=False)
    assert state["enabled"] is True
    status = companion_status(project)
    assert status["enabled"] is True
    assert status["process_alive"] is False

    stopped = stop_companion(project)
    assert stopped["enabled"] is False
    state_file = project / ".mojo" / "companion.json"
    assert json.loads(state_file.read_text(encoding="utf-8"))["enabled"] is False


def test_retrieval_plus_check_uses_scoped_mojo_knowledge(tmp_path, monkeypatch):
    db_path = tmp_path / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "repo"
    project.mkdir()
    init_db(db_path)
    db = get_db(db_path)
    save_knowledge(db, _item(
        project,
        id="db-001",
        type="anti_pattern",
        taxon="anti_pattern",
        title="Do not delete production records",
        content="Do not delete production records from this project.",
        applies_when="When cleanup commands touch production records.",
        tags=["production", "delete", "cleanup"],
        confidence=0.9,
    ))
    set_promotion_state(db, "db-001", "project_approved", scope="project")
    db.close()

    intervention = run_check(
        project,
        task="cleanup command will delete production records",
        notify=False,
    )
    assert intervention.intervention_type == "hard_warning"
    assert intervention.source_knowledge_ids == ["db-001"]
