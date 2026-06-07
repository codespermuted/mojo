import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from pathlib import Path

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
