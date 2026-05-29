import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import advisory
import db_ops
from advisory import (
    attach_project,
    detach_project,
    project_status,
    refresh_project,
    retrieve_advisory_context,
)
from db_ops import get_db, init_db, save_knowledge, set_promotion_state


def _use_temp_db(monkeypatch, db_path: Path):
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)
    monkeypatch.setattr(advisory, "DB_PATH", db_path)


def test_attach_detach_status_preserves_db(tmp_path, monkeypatch):
    db_path = tmp_path / "central" / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "project"
    project.mkdir()

    attach_project(project)
    status = project_status(project)
    assert status["attached"] is True
    assert Path(status["manifest_path"]).exists()
    assert status["db_path"] == str(db_path)

    result = detach_project(project)
    status = project_status(project)
    assert result["enabled"] is False
    assert status["attached"] is False
    assert db_path.exists()


def test_retrieval_uses_project_path_and_task(tmp_path, monkeypatch):
    db_path = tmp_path / "central" / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "project"
    project.mkdir()
    init_db(db_path)
    db = get_db(db_path)
    save_knowledge(db, {
        "id": "adv-001",
        "type": "debug_playbook",
        "domain": "web/fastapi",
        "title": "Check FastAPI dependency cache",
        "content": "For this project, inspect dependency cache when auth state looks stale.",
        "reasoning": "A prior bug came from cached dependencies.",
        "scope": "project",
        "applies_when": "When debugging FastAPI auth in this repository.",
        "does_not_apply_when": "Do not apply to non-FastAPI services.",
        "evidence_level": "local_rule",
        "promotion_state": "candidate",
        "project_path": str(project),
        "tags": ["fastapi", "auth", "cache"],
        "confidence": 0.8,
    })
    save_knowledge(db, {
        "id": "adv-002",
        "type": "tool_preference",
        "taxon": "deployment_note",
        "domain": "infra/k8s",
        "title": "Unrelated deployment note",
        "content": "Use the deployment runbook.",
        "reasoning": "Unrelated.",
        "scope": "project",
        "applies_when": "When deploying another project.",
        "project_path": str(tmp_path / "other"),
        "confidence": 0.8,
    })
    set_promotion_state(db, "adv-001", "project_approved", scope="project")
    db.close()

    items = retrieve_advisory_context(
        project, task="debug FastAPI auth cache", file_paths=["app/api/auth.py"], limit=3
    )
    assert [i["id"] for i in items][0] == "adv-001"
    assert "same project path" in items[0]["selected_because"]
    assert items[0]["tier"] == "T1"


def test_refresh_generates_advisory_mojo_md_only(tmp_path, monkeypatch):
    db_path = tmp_path / "central" / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "project"
    project.mkdir()
    init_db(db_path)
    db = get_db(db_path)
    save_knowledge(db, {
        "id": "md-001",
        "type": "domain_rule",
        "domain": "project/demo",
        "title": "Prefer the local helper",
        "content": "Use the local helper for this project.",
        "reasoning": "It wraps project-specific defaults.",
        "scope": "project",
        "applies_when": "When editing helper-backed code.",
        "does_not_apply_when": "Do not apply outside this project.",
        "evidence_level": "local_rule",
        "promotion_state": "candidate",
        "project_path": str(project),
        "confidence": 0.8,
    })
    set_promotion_state(db, "md-001", "project_approved", scope="project")
    db.close()

    out_path, items = refresh_project(project, task="edit helper-backed code", limit=5)
    assert out_path == project / "MOJO.md"
    content = out_path.read_text(encoding="utf-8")
    assert "Mojo Advisory Context" in content
    assert "Prefer the local helper" in content
    assert "This file is advisory context" in content
    assert len(items) == 1
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()


def test_detach_removes_only_managed_mojo_md(tmp_path, monkeypatch):
    db_path = tmp_path / "central" / "mojo.db"
    _use_temp_db(monkeypatch, db_path)
    project = tmp_path / "project"
    project.mkdir()
    attach_project(project)
    (project / "MOJO.md").write_text(
        f"{advisory.MOJO_START}\nmanaged\n{advisory.MOJO_END}\n",
        encoding="utf-8",
    )
    result = detach_project(project)
    assert result["removed_mojo_md"] is True
    assert not (project / "MOJO.md").exists()

    attach_project(project)
    (project / "MOJO.md").write_text("human note\n", encoding="utf-8")
    result = detach_project(project)
    assert result["removed_mojo_md"] is False
    assert (project / "MOJO.md").read_text(encoding="utf-8") == "human note\n"
