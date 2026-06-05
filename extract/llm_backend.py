"""LLM backend abstraction for the extraction pipeline.

Three interchangeable backends implement the same ``complete()`` contract:

- ``claude-cli``: headless ``claude -p`` — runs on the user's Claude
  subscription, zero API cost. Default.
- ``codex-cli``:  headless ``codex exec`` — runs on the user's Codex
  subscription, zero API cost.
- ``api``:        Anthropic API with prompt caching (the original path;
  needs ``ANTHROPIC_API_KEY``).

``complete()`` returns ``{"text", "usage", "cost_usd", "model"}`` where
``usage`` follows the Anthropic shape (input_tokens / output_tokens /
cache_read_input_tokens / cache_creation_input_tokens; zeros when the
backend cannot report them).

Recursion guard: CLI backends export ``MOJO_EXTRACTION=1`` into the child
process environment. Mojo's own Claude Code hooks check this variable and
bail, so an extraction run can never register itself as a new session and
trigger another extraction.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROLE_FILTER = "filter"
ROLE_STRUCTURE = "structure"

# Marker exported into CLI child processes; hooks/_resolve checks it.
EXTRACTION_ENV_FLAG = "MOJO_EXTRACTION"

DEFAULT_TIMEOUT_S = 600


class BackendError(RuntimeError):
    """A backend invocation failed (CLI missing, non-zero exit, bad output)."""


class LLMBackend:
    """Interface. Subclasses map a role (filter/structure) to a model."""

    name: str = "abstract"

    def complete(self, system: str, user: str, max_tokens: int,
                 role: str) -> dict:
        raise NotImplementedError

    def model_for(self, role: str) -> str:
        raise NotImplementedError


def _zero_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def _child_env() -> dict:
    env = dict(os.environ)
    env[EXTRACTION_ENV_FLAG] = "1"
    return env


class ClaudeCLIBackend(LLMBackend):
    """Headless ``claude -p``. Subscription-billed → cost_usd is 0."""

    name = "claude-cli"
    DEFAULT_MODELS = {ROLE_FILTER: "haiku", ROLE_STRUCTURE: "sonnet"}

    def __init__(self, models: dict | None = None,
                 timeout: int = DEFAULT_TIMEOUT_S):
        self.models = {**self.DEFAULT_MODELS, **(models or {})}
        self.timeout = timeout
        if shutil.which("claude") is None:
            raise BackendError(
                "claude CLI not found on PATH. Install Claude Code or "
                "switch backend: `mojo extract --backend api`."
            )

    def model_for(self, role: str) -> str:
        return self.models.get(role, self.DEFAULT_MODELS[ROLE_STRUCTURE])

    def complete(self, system: str, user: str, max_tokens: int,
                 role: str) -> dict:
        model = self.model_for(role)
        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--model", model,
            "--system-prompt", system,
            "--tools", "",
            "--no-session-persistence",
        ]
        try:
            proc = subprocess.run(
                cmd, input=user, capture_output=True, text=True,
                timeout=self.timeout, env=_child_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"claude -p timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise BackendError(
                f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}"
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise BackendError(
                f"claude -p returned non-JSON output: {proc.stdout[:300]}"
            ) from e
        if data.get("is_error"):
            raise BackendError(f"claude -p error result: {data.get('result', '')[:500]}")

        usage = data.get("usage") or {}
        return {
            "text": data.get("result", ""),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            },
            # Subscription-billed: no marginal cost. The JSON's
            # total_cost_usd is the API-equivalent price, not a charge.
            "cost_usd": 0.0,
            "model": f"{self.name}:{model}",
        }


class CodexCLIBackend(LLMBackend):
    """Headless ``codex exec``. Subscription-billed → cost_usd is 0.

    codex has no system-prompt flag, so the system text is prepended to
    the prompt. The final assistant message is read back via ``-o``.
    """

    name = "codex-cli"
    DEFAULT_MODELS: dict = {}  # empty → codex's configured default model

    def __init__(self, models: dict | None = None,
                 timeout: int = DEFAULT_TIMEOUT_S):
        self.models = {**self.DEFAULT_MODELS, **(models or {})}
        self.timeout = timeout
        if shutil.which("codex") is None:
            raise BackendError(
                "codex CLI not found on PATH. Install Codex or switch "
                "backend: `mojo extract --backend claude-cli`."
            )

    def model_for(self, role: str) -> str:
        return self.models.get(role) or "default"

    def complete(self, system: str, user: str, max_tokens: int,
                 role: str) -> dict:
        model = self.models.get(role)
        prompt = f"{system}\n\n---\n\n{user}"
        with tempfile.NamedTemporaryFile(
            mode="r", suffix=".txt", prefix="mojo-codex-", delete=False
        ) as out:
            out_path = Path(out.name)
        try:
            cmd = [
                "codex", "exec", "-",
                "--ephemeral",
                "--skip-git-repo-check",
                "-s", "read-only",
                "--color", "never",
                "-o", str(out_path),
            ]
            if model:
                cmd += ["-m", model]
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    timeout=self.timeout, env=_child_env(),
                )
            except subprocess.TimeoutExpired as e:
                raise BackendError(
                    f"codex exec timed out after {self.timeout}s") from e
            if proc.returncode != 0:
                raise BackendError(
                    f"codex exec exited {proc.returncode}: "
                    f"{proc.stderr.strip()[:500]}"
                )
            text = out_path.read_text(encoding="utf-8").strip()
            if not text:
                raise BackendError("codex exec produced no final message")
        finally:
            out_path.unlink(missing_ok=True)

        return {
            "text": text,
            "usage": _zero_usage(),  # codex exec does not report token usage
            "cost_usd": 0.0,
            "model": f"{self.name}:{model or 'default'}",
        }


class AnthropicAPIBackend(LLMBackend):
    """Anthropic API with ephemeral prompt caching (original behavior)."""

    name = "api"
    DEFAULT_MODELS = {
        ROLE_FILTER: "claude-haiku-4-5-20251001",
        ROLE_STRUCTURE: "claude-sonnet-4-6",
    }

    def __init__(self, models: dict | None = None,
                 timeout: int = DEFAULT_TIMEOUT_S):
        self.models = {**self.DEFAULT_MODELS, **(models or {})}
        self.timeout = timeout
        import anthropic  # lazy: CLI-backend users don't need the package
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def model_for(self, role: str) -> str:
        return self.models.get(role, self.DEFAULT_MODELS[ROLE_STRUCTURE])

    def complete(self, system: str, user: str, max_tokens: int,
                 role: str) -> dict:
        model = self.model_for(role)
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        usage = response.usage
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        return {
            "text": response.content[0].text or "",
            "usage": usage_dict,
            "cost_usd": _api_cost(model, usage_dict),
            "model": model,
        }


def _api_cost(model: str, usage: dict) -> float:
    """API cost in USD including cache discounts (per-Mtok pricing)."""
    if "haiku" in model:
        in_price, out_price = 0.25, 1.25
    elif "sonnet" in model:
        in_price, out_price = 3.0, 15.0
    else:
        in_price, out_price = 15.0, 75.0
    base = (usage.get("input_tokens", 0) * in_price
            + usage.get("output_tokens", 0) * out_price) / 1_000_000
    cache = (usage.get("cache_read_input_tokens", 0) * in_price * 0.1
             + usage.get("cache_creation_input_tokens", 0) * in_price * 1.25
             ) / 1_000_000
    return base + cache


BACKENDS = {
    ClaudeCLIBackend.name: ClaudeCLIBackend,
    CodexCLIBackend.name: CodexCLIBackend,
    AnthropicAPIBackend.name: AnthropicAPIBackend,
}


def get_backend(name: str | None = None, config: dict | None = None) -> LLMBackend:
    """Resolve the backend by explicit name > env > config > default.

    Model overrides come from ``extraction.models.<backend>`` in config:

        extraction:
          backend: claude-cli
          models:
            claude-cli: {filter: haiku, structure: sonnet}
            api: {filter: claude-haiku-4-5-20251001, structure: claude-sonnet-4-6}
    """
    config = config or {}
    extraction_cfg = config.get("extraction") or {}
    resolved = (
        name
        or os.environ.get("MOJO_LLM_BACKEND")
        or extraction_cfg.get("backend")
        or ClaudeCLIBackend.name
    )
    if resolved not in BACKENDS:
        raise BackendError(
            f"Unknown backend '{resolved}'. "
            f"Valid: {', '.join(sorted(BACKENDS))}"
        )
    models_cfg = (extraction_cfg.get("models") or {}).get(resolved) or {}
    timeout = int(extraction_cfg.get("cli_timeout_s", DEFAULT_TIMEOUT_S))
    return BACKENDS[resolved](models=models_cfg, timeout=timeout)
