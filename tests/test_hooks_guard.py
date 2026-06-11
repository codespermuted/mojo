import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from pathlib import Path

import on_session_end
from _resolve import (
    auto_extract_enabled, is_extraction_context, should_ignore_cwd,
)


def test_extraction_context_guard(monkeypatch):
    monkeypatch.delenv("MOJO_EXTRACTION", raising=False)
    assert is_extraction_context() is False
    monkeypatch.setenv("MOJO_EXTRACTION", "1")
    assert is_extraction_context() is True


def test_auto_extract_enabled_default_true(tmp_path):
    # missing config → default on
    assert auto_extract_enabled(tmp_path) is True


def test_auto_extract_enabled_reads_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("extraction:\n  auto_extract: false\n", encoding="utf-8")
    assert auto_extract_enabled(tmp_path) is False
    cfg.write_text("extraction:\n  auto_extract: true\n", encoding="utf-8")
    assert auto_extract_enabled(tmp_path) is True


def test_should_ignore_cwd():
    # mojo's own repo (this test lives two levels under it)
    mojo_repo = Path(__file__).resolve().parent.parent
    assert should_ignore_cwd(str(mojo_repo)) is True
    assert should_ignore_cwd(str(mojo_repo / "extract")) is True
    # bare home dir
    assert should_ignore_cwd(str(Path.home())) is True
    # empty cwd → ignore (can't route safely)
    assert should_ignore_cwd("") is True
    assert should_ignore_cwd(None) is True
    # a normal project dir → capture
    assert should_ignore_cwd("/home/whoever/wd/projects/realproj") is False


def test_spawn_auto_extract_sets_extraction_flag(tmp_path, monkeypatch):
    """The spawned `mojo extract` subtree must carry MOJO_EXTRACTION=1 so
    its headless `claude -p` sessions bail instead of recursing. Regression
    guard for the runaway-fan-out bug that burned the token budget."""
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")

        class _P:  # noqa: D401 - stub
            pass
        return _P()

    monkeypatch.setattr(on_session_end.shutil, "which", lambda _: "/usr/bin/mojo")
    monkeypatch.setattr(on_session_end.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("MOJO_EXTRACTION", raising=False)

    mojo_db = tmp_path / ".mojo" / "mojo.db"
    mojo_db.parent.mkdir(parents=True)
    on_session_end._spawn_auto_extract(mojo_db, "sess-1", "/tmp/transcript.jsonl")

    assert captured["env"]["MOJO_EXTRACTION"] == "1"
    assert captured["env"]["MOJO_HOME"] == str(mojo_db.parent)
