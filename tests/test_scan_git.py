"""Regression tests for scan.git_log multi-line body parsing.

Background: the previous implementation used ``--pretty=format:...%b``
without ``-z``, then split stdout on newlines. Multi-line commit bodies
got truncated to their first line, which silently broke
``classify_commit``'s ``len(body) > 100`` domain-rule fallback. Repos
with rich commit messages but no ``fix:``/``revert:`` keywords ended up
yielding zero candidates.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scan import classify_commit, git_log


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo_with_long_body(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    (repo / "a.txt").write_text("hello\n")
    _git(repo, "add", "a.txt")

    # Body is intentionally free of fix:/revert:/decision-verb signals
    # ("instead of", "replace", "migrate", "never", ...) so the only
    # classifier path that can fire is the long-body domain_rule fallback.
    long_body = (
        "First paragraph of background that explains the change.\n"
        "\n"
        "Second paragraph with more detail about the chosen approach,\n"
        "including a couple of trade-offs and the specific failure mode\n"
        "the previous code hit in production under high load.\n"
    )
    _git(repo, "commit", "-q", "-m", "Add a.txt with seed content",
         "-m", long_body)
    return repo


def test_git_log_preserves_multiline_body(repo_with_long_body: Path):
    commits = git_log(str(repo_with_long_body))
    assert len(commits) == 1
    body = commits[0]["body"]
    # All three signal phrases live on different lines of the body.
    assert "First paragraph" in body
    assert "Second paragraph" in body
    assert "specific failure mode" in body
    assert len(body) > 100


def test_classify_commit_picks_up_long_body(repo_with_long_body: Path):
    commits = git_log(str(repo_with_long_body))
    classification = classify_commit(commits[0])
    assert classification is not None
    # Subject has no fix:/revert:/decision-verb signal, so the long-body
    # fallback is the only thing that can fire.
    assert classification["type"] == "domain_rule"


def test_git_log_handles_pipe_in_subject(tmp_path: Path):
    """A literal ``|||`` in the subject must not corrupt parsing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m",
         "fix: handle ||| literal sequence in subject", "-m",
         "Body line one.\nBody line two.\nBody line three with details.\n"
         "Body line four to push past 100 chars for the fallback rule.")
    commits = git_log(str(repo))
    assert len(commits) == 1
    # split("|||", 4) caps at 5 parts so the literal ||| stays in body
    # rather than splitting the record into garbage.
    assert "Body line three" in commits[0]["body"]
