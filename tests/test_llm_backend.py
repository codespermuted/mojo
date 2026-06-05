import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract import llm_backend
from extract.llm_backend import (
    BackendError, ClaudeCLIBackend, CodexCLIBackend,
    EXTRACTION_ENV_FLAG, get_backend,
)


@pytest.fixture
def cli_available(monkeypatch):
    monkeypatch.setattr(llm_backend.shutil, "which", lambda name: f"/usr/bin/{name}")


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_claude_cli_complete(cli_available, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        payload = {
            "type": "result", "is_error": False,
            "result": '{"has_knowledge": false}',
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 100},
        }
        return FakeProc(stdout=json.dumps(payload))

    monkeypatch.setattr(llm_backend.subprocess, "run", fake_run)

    backend = ClaudeCLIBackend()
    result = backend.complete("sys prompt", "user text", max_tokens=100,
                              role="filter")

    assert result["text"] == '{"has_knowledge": false}'
    assert result["cost_usd"] == 0.0  # subscription — never billed as cost
    assert result["usage"]["input_tokens"] == 10
    assert result["model"] == "claude-cli:haiku"
    # recursion guard exported to the child process
    assert captured["env"][EXTRACTION_ENV_FLAG] == "1"
    # API key stripped → subscription auth forced, zero-cost claim honest
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    # headless flags present
    assert "--no-session-persistence" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "haiku"


def test_claude_cli_structure_uses_structure_model(cli_available, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc(stdout=json.dumps({"is_error": False, "result": "{}",
                                           "usage": {}}))

    monkeypatch.setattr(llm_backend.subprocess, "run", fake_run)
    backend = ClaudeCLIBackend(models={"structure": "opus"})
    backend.complete("s", "u", max_tokens=10, role="structure")
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "opus"


def test_claude_cli_error_raises(cli_available, monkeypatch):
    monkeypatch.setattr(
        llm_backend.subprocess, "run",
        lambda *a, **k: FakeProc(returncode=1, stderr="boom"))
    backend = ClaudeCLIBackend()
    with pytest.raises(BackendError):
        backend.complete("s", "u", max_tokens=10, role="filter")


def test_codex_cli_complete(cli_available, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text('{"ok": true}', encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(llm_backend.subprocess, "run", fake_run)

    backend = CodexCLIBackend()
    result = backend.complete("sys", "user", max_tokens=100, role="filter")

    assert result["text"] == '{"ok": true}'
    assert result["cost_usd"] == 0.0
    assert "--ephemeral" in captured["cmd"]
    assert "read-only" in captured["cmd"]


def test_missing_cli_raises(monkeypatch):
    monkeypatch.setattr(llm_backend.shutil, "which", lambda name: None)
    with pytest.raises(BackendError):
        ClaudeCLIBackend()


def test_get_backend_resolution(cli_available, monkeypatch):
    monkeypatch.delenv("MOJO_LLM_BACKEND", raising=False)
    # default
    assert get_backend(config={}).name == "claude-cli"
    # config
    assert get_backend(config={"extraction": {"backend": "codex-cli"}}).name == "codex-cli"
    # env beats config
    monkeypatch.setenv("MOJO_LLM_BACKEND", "claude-cli")
    assert get_backend(config={"extraction": {"backend": "codex-cli"}}).name == "claude-cli"
    # explicit beats env
    assert get_backend("codex-cli", config={}).name == "codex-cli"
    # unknown
    with pytest.raises(BackendError):
        get_backend("nope", config={})


def test_get_backend_model_overrides(cli_available, monkeypatch):
    monkeypatch.delenv("MOJO_LLM_BACKEND", raising=False)
    cfg = {"extraction": {"backend": "claude-cli",
                          "models": {"claude-cli": {"filter": "sonnet"}}}}
    backend = get_backend(config=cfg)
    assert backend.model_for("filter") == "sonnet"
    assert backend.model_for("structure") == "sonnet"  # default kept
