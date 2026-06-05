import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import db_ops
from db_ops import get_db, init_db, record_observation, save_knowledge
from serve import vault as vault_mod
from serve.vault import (
    export_vault, import_vault_edits, render_review_queue, sync_vault,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated DB + vault, with vault.load_config patched to point at them."""
    db_path = tmp_path / "mojo.db"
    vault_path = tmp_path / "vault"
    monkeypatch.setattr(db_ops, "DB_PATH", db_path)
    monkeypatch.setattr(
        vault_mod, "load_config",
        lambda: {"vault": {"path": str(vault_path)}})
    init_db(db_path)
    return {"db_path": db_path, "vault": vault_path}


def _seed(db_path, item_id="smp-001"):
    db = get_db(db_path)
    save_knowledge(db, {
        "id": item_id,
        "type": "domain_rule",
        "domain": "electricity/smp",
        "title": "Use KST for SMP timestamps",
        "content": "Always use KST for SMP data.",
        "reasoning": "Market operator publishes in KST.",
        "confidence": 0.9,
        "scope": "project",
        "project_path": "/home/u/proj-a",
        "tags": ["smp", "kst"],
    })
    record_observation(db, item_id, project_path="/home/u/proj-a",
                       session_id="s1", stance="supports", note="initial")
    db.close()


def _note_path(vault: Path, item_id: str) -> Path:
    matches = list((vault / "knowledge").rglob(f"{item_id} *.md"))
    assert len(matches) == 1, f"expected one note for {item_id}, got {matches}"
    return matches[0]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    block = text.split("---")[1]
    return yaml.safe_load(block)


def test_export_writes_note_with_frontmatter(env):
    _seed(env["db_path"])
    export_vault(quiet=True)

    note = _note_path(env["vault"], "smp-001")
    assert "electricity" in str(note)  # domain → folder hierarchy
    meta = _frontmatter(note)
    assert meta["id"] == "smp-001"
    assert meta["scope"] == "project"
    assert meta["grade"] in "ABCDF"
    assert meta["tags"] == ["smp", "kst"]
    body = note.read_text(encoding="utf-8")
    assert "## Observations" in body
    assert "proj-a" in body
    # review queue generated at vault root
    assert (env["vault"] / "REVIEW-QUEUE.md").exists()


def test_import_applies_human_edits(env):
    _seed(env["db_path"])
    export_vault(quiet=True)
    note = _note_path(env["vault"], "smp-001")

    text = note.read_text(encoding="utf-8")
    text = text.replace("promotion_state: candidate",
                        "promotion_state: project_approved")
    text = text.replace("applies_when: ''",
                        "applies_when: 'SMP 데이터 처리 시'")
    note.write_text(text, encoding="utf-8")

    changed = import_vault_edits(quiet=True)
    assert changed == 1

    db = get_db(env["db_path"])
    row = db.execute(
        "SELECT promotion_state, approved, applies_when FROM knowledge "
        "WHERE id='smp-001'").fetchone()
    db.close()
    # promotion routed through set_promotion_state → approved side effect
    assert row["promotion_state"] == "project_approved"
    assert row["approved"] == 1
    assert row["applies_when"] == "SMP 데이터 처리 시"


def test_invalid_enum_edit_is_ignored(env):
    _seed(env["db_path"])
    export_vault(quiet=True)
    note = _note_path(env["vault"], "smp-001")
    text = note.read_text(encoding="utf-8").replace(
        "scope: project", "scope: galactic")
    note.write_text(text, encoding="utf-8")

    import_vault_edits(quiet=True)

    db = get_db(env["db_path"])
    row = db.execute("SELECT scope FROM knowledge WHERE id='smp-001'").fetchone()
    db.close()
    assert row["scope"] == "project"  # bad value rejected


def test_sync_roundtrip_is_stable(env):
    """sync → no edits → sync must not flip-flop file contents."""
    _seed(env["db_path"])
    sync_vault(quiet=True)
    note = _note_path(env["vault"], "smp-001")
    first = note.read_text(encoding="utf-8")
    sync_vault(quiet=True)
    assert note.read_text(encoding="utf-8") == first


def test_title_edit_renames_file(env):
    _seed(env["db_path"])
    export_vault(quiet=True)
    note = _note_path(env["vault"], "smp-001")
    text = note.read_text(encoding="utf-8").replace(
        "title: Use KST for SMP timestamps", "title: SMP는 KST 고정")
    note.write_text(text, encoding="utf-8")

    sync_vault(quiet=True)

    renamed = _note_path(env["vault"], "smp-001")
    assert "SMP는 KST 고정" in renamed.name


def test_review_queue_lists_generalization_candidates(env):
    _seed(env["db_path"])
    db = get_db(env["db_path"])
    record_observation(db, "smp-001", project_path="/home/u/proj-b",
                       stance="supports")
    db.close()
    export_vault(quiet=True)

    queue = (env["vault"] / "REVIEW-QUEUE.md").read_text(encoding="utf-8")
    assert "일반화 후보 (1)" in queue
    assert "smp-001" in queue
