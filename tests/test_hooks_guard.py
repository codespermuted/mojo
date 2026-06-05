import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from _resolve import auto_extract_enabled, is_extraction_context


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
