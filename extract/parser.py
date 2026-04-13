"""Parse Claude Code session JSONL transcripts into structured turns."""

import json
from pathlib import Path
from typing import Optional


def parse_session(jsonl_path: str) -> dict:
    """Parse a Claude Code JSONL transcript into structured data.
    
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
        if not session_id and "sessionId" in event:
            session_id = event["sessionId"]
        if not project_path and "cwd" in event:
            project_path = event["cwd"]

        ts = event.get("timestamp", "")
        if ts:
            timestamps.append(ts)

        event_type = event.get("type", "")
        message = event.get("message", {})

        if event_type == "user":
            content = _extract_content(message.get("content", ""))
            if content:
                turns.append({
                    "role": "user",
                    "content": content,
                    "timestamp": ts,
                    "tool_uses": [],
                })

        elif event_type == "assistant":
            content_blocks = message.get("content", [])
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
