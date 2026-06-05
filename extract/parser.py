"""Parse Claude Code / Codex session JSONL transcripts into structured turns."""

import json
import re
from pathlib import Path
from typing import Optional

# Codex rollout files wrap every event as {"timestamp", "type", "payload"}.
_CODEX_EVENT_TYPES = {"session_meta", "response_item", "event_msg",
                      "turn_context", "compacted"}

# Codex injects harness context as user-role messages; these carry no
# tacit knowledge and would pollute signal detection.
_CODEX_SYNTHETIC_PREFIX = re.compile(
    r"\A<(environment_context|goal_context|permissions|user_instructions|"
    r"turn_aborted|AGENTS|system)", re.IGNORECASE)


def parse_session(jsonl_path: str) -> dict:
    """Parse a Claude Code or Codex JSONL transcript into structured data.

    The format is auto-detected from the first event line.

    Returns:
        {
            "session_id": str,
            "project_path": str,
            "turns": [{"role": str, "content": str, "timestamp": str, "tool_uses": list}],
            "turn_count": int,
            "started_at": str,
            "ended_at": str,
        }
    """
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {jsonl_path}")

    if _is_codex_rollout(path):
        return _parse_codex_session(path)

    turns = []
    session_id = None
    project_path = None
    timestamps = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Extract session metadata
        if not session_id:
            session_id = (
                event.get("sessionId")
                or event.get("session_id")
                or event.get("conversation_id")
            )
        if not project_path:
            project_path = (
                event.get("cwd")
                or event.get("project_path")
                or event.get("workspace")
                or event.get("repo")
            )

        ts = event.get("timestamp", "")
        if ts:
            timestamps.append(ts)

        event_type = event.get("type", "")
        message = event.get("message", {})
        role = event.get("role") or message.get("role", "")

        if event_type == "user" or (not event_type and role == "user"):
            raw_content = message.get("content", "") if message else event.get("content", "")
            content = _extract_content(raw_content)
            if content:
                turns.append({
                    "role": "user",
                    "content": content,
                    "timestamp": ts,
                    "tool_uses": [],
                })

        elif event_type == "assistant" or (not event_type and role == "assistant"):
            content_blocks = message.get("content", []) if message else event.get("content", "")
            text_content = ""
            tool_uses = []

            if isinstance(content_blocks, str):
                text_content = content_blocks
            elif isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_content += block.get("text", "") + "\n"
                        elif block.get("type") == "tool_use":
                            tool_uses.append({
                                "name": block.get("name", ""),
                                "input": _truncate(
                                    json.dumps(block.get("input", {}), ensure_ascii=False),
                                    max_len=500
                                ),
                            })

            if text_content.strip() or tool_uses:
                turns.append({
                    "role": "assistant",
                    "content": text_content.strip(),
                    "timestamp": ts,
                    "tool_uses": tool_uses,
                })

    return {
        "session_id": session_id or path.stem,
        "project_path": project_path or "",
        "turns": turns,
        "turn_count": len(turns),
        "started_at": timestamps[0] if timestamps else "",
        "ended_at": timestamps[-1] if timestamps else "",
    }


def _is_codex_rollout(path: Path) -> bool:
    """Detect a Codex rollout file from its first parseable event."""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return False
                return (isinstance(event, dict)
                        and "payload" in event
                        and event.get("type") in _CODEX_EVENT_TYPES)
    except OSError:
        return False
    return False


def _codex_text(content) -> str:
    """Join input_text/output_text blocks from a Codex message payload."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                    "input_text", "output_text", "text"):
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _parse_codex_session(path: Path) -> dict:
    """Parse a Codex rollout JSONL into the same shape as Claude sessions."""
    turns: list[dict] = []
    session_id = None
    project_path = None
    timestamps: list[str] = []
    pending_tools: list[dict] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = event.get("timestamp", "")
        if ts:
            timestamps.append(ts)
        etype = event.get("type", "")
        payload = event.get("payload") or {}

        if etype == "session_meta":
            session_id = payload.get("id") or session_id
            project_path = payload.get("cwd") or project_path
            continue

        if etype != "response_item":
            continue

        ptype = payload.get("type", "")
        if ptype in ("function_call", "custom_tool_call"):
            pending_tools.append({
                "name": payload.get("name", ""),
                "input": _truncate(str(payload.get("arguments")
                                       or payload.get("input") or ""),
                                   max_len=500),
            })
            continue

        if ptype != "message":
            continue

        role = payload.get("role", "")
        text = _codex_text(payload.get("content")).strip()
        if role == "user":
            if not text or _CODEX_SYNTHETIC_PREFIX.match(text):
                continue
            turns.append({"role": "user", "content": text,
                          "timestamp": ts, "tool_uses": []})
        elif role == "assistant":
            if not text and not pending_tools:
                continue
            turns.append({"role": "assistant", "content": text,
                          "timestamp": ts, "tool_uses": pending_tools})
            pending_tools = []

    return {
        "session_id": session_id or path.stem,
        "project_path": project_path or "",
        "turns": turns,
        "turn_count": len(turns),
        "started_at": timestamps[0] if timestamps else "",
        "ended_at": timestamps[-1] if timestamps else "",
    }


def turns_to_conversation_text(turns: list[dict], max_tokens: int = 15000) -> str:
    """Convert turns into a readable conversation text for LLM analysis.
    
    Truncates to approximate max_tokens (4 chars ≈ 1 token).
    """
    lines = []
    char_budget = max_tokens * 4

    for turn in turns:
        role = "USER" if turn["role"] == "user" else "CLAUDE"
        content = turn["content"]

        # Include tool use summaries for assistant turns
        if turn["tool_uses"]:
            tools = ", ".join(t["name"] for t in turn["tool_uses"])
            content += f"\n[Tools used: {tools}]"

        lines.append(f"[{role}]: {content}")

    text = "\n\n".join(lines)

    # Truncate from the middle if too long, keeping start and end
    if len(text) > char_budget:
        half = char_budget // 2
        text = text[:half] + "\n\n[... middle truncated ...]\n\n" + text[-half:]

    return text


def _extract_content(content) -> str:
    """Extract text from various content formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return ""


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
