"""Tests for Mojo parser and signal detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract.parser import parse_session, turns_to_conversation_text
from extract.signals import detect_corrections, detect_domain_signals, score_session_value
from extract.dedup import is_duplicate, find_related

FIXTURE_PATH = str(Path(__file__).parent / "fixtures" / "sample_session.jsonl")


class TestParser:
    def test_parse_session_basic(self):
        result = parse_session(FIXTURE_PATH)
        assert result["session_id"] == "test-session-001"
        assert result["turn_count"] == 8  # 4 user + 4 assistant
        assert result["project_path"] == "/home/user/example-project"

    def test_parse_turns_structure(self):
        result = parse_session(FIXTURE_PATH)
        turns = result["turns"]
        assert turns[0]["role"] == "user"
        assert "pagination" in turns[0]["content"]
        assert turns[1]["role"] == "assistant"

    def test_conversation_text(self):
        result = parse_session(FIXTURE_PATH)
        text = turns_to_conversation_text(result["turns"])
        assert "[USER]:" in text
        assert "[CLAUDE]:" in text
        assert "cursor" in text

    def test_conversation_text_truncation(self):
        result = parse_session(FIXTURE_PATH)
        text = turns_to_conversation_text(result["turns"], max_tokens=50)
        # Should be truncated
        assert len(text) <= 50 * 4 + 100  # rough bound


class TestSignals:
    def test_detect_corrections(self):
        result = parse_session(FIXTURE_PATH)
        corrections = detect_corrections(result["turns"])
        # "아니야, 그게 아니라" should trigger
        assert len(corrections) >= 1
        assert corrections[0]["signal_type"] == "correction"
        assert "offset" in corrections[0]["claude_said"]

    def test_detect_domain_signals(self):
        result = parse_session(FIXTURE_PATH)
        signals = detect_domain_signals(result["turns"])
        assert isinstance(signals, list)

    def test_score_session_value(self):
        result = parse_session(FIXTURE_PATH)
        value = score_session_value(result["turns"])
        assert value["should_extract"] is True
        assert value["corrections"] >= 1
        assert value["score"] > 0.0


class TestDedup:
    def test_not_duplicate(self):
        is_dup, sim = is_duplicate(
            "REST API에서 cursor 기반 pagination이 중요하다",
            ["Docker 컨테이너의 OOM 디버깅 방법"]
        )
        assert is_dup is False

    def test_is_duplicate(self):
        is_dup, sim = is_duplicate(
            "ORM loop 안에서 개별 row 조회는 N+1 anti-pattern",
            ["ORM loop 안 row 개별 조회는 N+1 anti-pattern 패턴"]
        )
        assert is_dup is True
        assert sim > 0.7

    def test_find_related(self):
        existing = [
            {"id": "api-001", "content": "REST API pagination은 cursor 기반을 사용"},
            {"id": "infra-001", "content": "Docker 컨테이너 OOM 디버깅"},
            {"id": "api-002", "content": "N+1 쿼리 패턴 금지"},
        ]
        related = find_related("API pagination에서 cursor 방식", existing)
        assert isinstance(related, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
