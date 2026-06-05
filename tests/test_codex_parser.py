import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract.parser import parse_session


def _write_rollout(path: Path, cwd: str = "/home/user/proj"):
    events = [
        {"timestamp": "2026-05-29T02:39:25.552Z", "type": "session_meta",
         "payload": {"id": "codex-session-001", "cwd": cwd,
                     "originator": "codex-tui"}},
        # synthetic harness messages must be filtered out
        {"timestamp": "t1", "type": "response_item",
         "payload": {"type": "message", "role": "developer",
                     "content": [{"type": "input_text",
                                  "text": "<permissions instructions>..."}]}},
        {"timestamp": "t2", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text",
                                  "text": "<environment_context><cwd>/x</cwd></environment_context>"}]}},
        # real conversation
        {"timestamp": "t3", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text",
                                  "text": "SMP 데이터는 항상 KST로 처리해줘"}]}},
        {"timestamp": "t4", "type": "response_item",
         "payload": {"type": "reasoning", "summary": []}},
        {"timestamp": "t5", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": "{\"command\": [\"ls\"]}"}},
        {"timestamp": "t6", "type": "response_item",
         "payload": {"type": "function_call_output", "output": "..."}},
        {"timestamp": "t7", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "KST 기준으로 변환했습니다."}]}},
        {"timestamp": "t8", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "noise"}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def test_codex_rollout_detected_and_parsed(tmp_path):
    rollout = tmp_path / "rollout-2026-05-29T11-37-43-abc.jsonl"
    _write_rollout(rollout)

    data = parse_session(str(rollout))

    assert data["session_id"] == "codex-session-001"
    assert data["project_path"] == "/home/user/proj"
    # developer + environment_context messages filtered, 2 real turns left
    assert data["turn_count"] == 2
    assert data["turns"][0]["role"] == "user"
    assert "KST" in data["turns"][0]["content"]
    assert data["turns"][1]["role"] == "assistant"
    # function_call before the assistant message lands in its tool_uses
    assert data["turns"][1]["tool_uses"][0]["name"] == "shell"


def test_claude_format_still_parses(tmp_path):
    """Format detection must not break the original Claude Code path."""
    transcript = tmp_path / "claude.jsonl"
    events = [
        {"type": "user", "sessionId": "claude-1", "cwd": "/home/user/p2",
         "timestamp": "t1", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": "t2",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "hi"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events),
                          encoding="utf-8")

    data = parse_session(str(transcript))
    assert data["session_id"] == "claude-1"
    assert data["project_path"] == "/home/user/p2"
    assert data["turn_count"] == 2
