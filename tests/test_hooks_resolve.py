"""Tests for hooks/_resolve.py — the runtime MOJO_HOME resolver.

These cover the policy the hook scripts rely on to route session
events to the right mojo.db without the global ``MOJO_HOME=...``
prefix the old init baked into settings.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from _resolve import resolve_mojo_db  # noqa: E402


def _make_store(root: Path) -> Path:
    """Create a minimal .mojo/mojo.db file under ``root`` and return its path."""
    store = root / ".mojo"
    store.mkdir(parents=True, exist_ok=True)
    db = store / "mojo.db"
    db.write_bytes(b"")  # resolver only checks existence
    return db


class TestResolveMojoDb:
    def test_env_var_override_wins(self, tmp_path, monkeypatch):
        # A project sidecar sits in cwd, and a custom MOJO_HOME points
        # somewhere else. The env var must take priority — this is the
        # explicit pinning escape hatch, used by tests and advanced users.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_store(proj)

        pinned = tmp_path / "pinned"
        pinned.mkdir()
        pinned_db = pinned / "mojo.db"
        pinned_db.write_bytes(b"")

        monkeypatch.setenv("MOJO_HOME", str(pinned))
        assert resolve_mojo_db(str(proj)) == pinned_db

    def test_env_var_override_returns_none_if_missing(self, tmp_path, monkeypatch):
        # Honour the env var even when it points at nothing, rather
        # than silently falling through to a different store. Silent
        # fallback would mask config mistakes.
        missing = tmp_path / "nope"
        monkeypatch.setenv("MOJO_HOME", str(missing))
        assert resolve_mojo_db(str(tmp_path)) is None

    def test_walk_up_finds_project_sidecar(self, tmp_path, monkeypatch):
        # The default per-project isolation path: .mojo sits at the
        # project root and Claude Code reports cwd == project root.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        proj = tmp_path / "proj"
        proj.mkdir()
        db = _make_store(proj)
        # Point HOME at a location with no global store, so a bug that
        # falls through to the global path would surface as a mismatch.
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))
        assert resolve_mojo_db(str(proj)) == db

    def test_walk_up_from_subdir(self, tmp_path, monkeypatch):
        # Claude Code reports cwd as wherever the user invoked it,
        # which is frequently a subdirectory of the project. The
        # resolver walks up parents until it hits a .mojo — this is
        # the load-bearing behaviour that makes "drop .mojo anywhere"
        # feel seamless.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        proj = tmp_path / "proj"
        db = _make_store(proj)
        deep = proj / "src" / "pkg" / "inner"
        deep.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))
        assert resolve_mojo_db(str(deep)) == db

    def test_nearest_sidecar_wins_over_outer(self, tmp_path, monkeypatch):
        # Nested projects: the innermost .mojo should win so that a
        # sub-project's sessions never leak into the enclosing one.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        outer = tmp_path / "outer"
        outer_db = _make_store(outer)
        inner = outer / "sub"
        inner_db = _make_store(inner)
        assert outer_db != inner_db
        monkeypatch.setenv("HOME", str(tmp_path / "no_home"))
        assert resolve_mojo_db(str(inner)) == inner_db

    def test_fallback_to_global_store(self, tmp_path, monkeypatch):
        # cwd has no .mojo anywhere on the way up → use ~/.mojo.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        home = tmp_path / "home"
        global_db = _make_store(home)
        loose = tmp_path / "loose"
        loose.mkdir()
        monkeypatch.setenv("HOME", str(home))
        assert resolve_mojo_db(str(loose)) == global_db

    def test_returns_none_when_nothing_exists(self, tmp_path, monkeypatch):
        # No sidecar, no global — mojo init has never been run. The
        # hook must bail silently rather than create a DB mid-session.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "blank_home"))
        loose = tmp_path / "loose"
        loose.mkdir()
        assert resolve_mojo_db(str(loose)) is None

    def test_empty_cwd_falls_back_to_global(self, tmp_path, monkeypatch):
        # Hook payloads occasionally arrive with cwd="" (older Claude
        # Code builds, headless runners). Treat that as "no project
        # context" and go straight to the global store instead of
        # crashing on Path("").parents.
        monkeypatch.delenv("MOJO_HOME", raising=False)
        home = tmp_path / "home"
        global_db = _make_store(home)
        monkeypatch.setenv("HOME", str(home))
        assert resolve_mojo_db("") == global_db

    def test_none_cwd_falls_back_to_global(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MOJO_HOME", raising=False)
        home = tmp_path / "home"
        global_db = _make_store(home)
        monkeypatch.setenv("HOME", str(home))
        assert resolve_mojo_db(None) == global_db
