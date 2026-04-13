"""Detect high-value knowledge signals in Claude Code sessions."""

import re
from typing import Optional

# Correction patterns: user telling Claude it's wrong
CORRECTION_PATTERNS_KO = [
    r"아니[야요라],?\s",
    r"그게\s*아니라",
    r"잘못\s*됐|틀렸",
    r"이렇게\s*(하|바꿔|수정|고쳐)",
    r"우리\s*(도메인|회사|팀|프로젝트)에서는",
    r"실제로는",
    r"현실에서는",
    r"그건\s*(안|못)\s*(돼|됨|되)",
    r"절대\s*(하면|쓰면)\s*안",
    r"반드시\s",
]

CORRECTION_PATTERNS_EN = [
    r"\bnot?\b.*\binstead\b",
    r"\bwrong\b",
    r"\bactually\b",
    r"\bdon'?t\s+use\b",
    r"\bnever\s+use\b",
    r"\balways\s+use\b",
    r"\bin\s+our\s+(domain|project|codebase)\b",
    r"\bthat'?s\s+not\s+(right|correct|how)\b",
    r"\bshould\s+be\b.*\bnot\b",
]

ALL_PATTERNS = CORRECTION_PATTERNS_KO + CORRECTION_PATTERNS_EN

# Domain knowledge indicators (not corrections, but explicit domain rules)
DOMAIN_SIGNAL_PATTERNS = [
    r"(핵심|중요한|필수)\s*(규칙|원칙|제약|포인트)",
    r"기억해\s*(둬|줘)",
    r"항상\s.*해야\s*(해|함|됨)",
    r"(rule|principle|constraint|convention)\s*(is|:)",
    r"(?:이건|여기서는)\s*특별히",
]


def detect_corrections(turns: list[dict]) -> list[dict]:
    """Detect correction signals: user correcting Claude's domain assumptions.
    
    Returns list of correction contexts with preceding Claude response.
    """
    corrections = []

    for i, turn in enumerate(turns):
        if turn["role"] != "user" or i == 0:
            continue

        content = turn["content"]
        matched_patterns = []

        for pattern in ALL_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                matched_patterns.append(pattern)

        if matched_patterns:
            # Get the preceding Claude response
            prev_claude = ""
            for j in range(i - 1, -1, -1):
                if turns[j]["role"] == "assistant":
                    prev_claude = turns[j]["content"][:500]
                    break

            corrections.append({
                "turn_index": i,
                "claude_said": prev_claude,
                "user_corrected": content,
                "signal_type": "correction",
                "signal_strength": _correction_strength(matched_patterns),
                "matched_patterns": len(matched_patterns),
            })

    return corrections


def detect_domain_signals(turns: list[dict]) -> list[dict]:
    """Detect explicit domain knowledge statements."""
    signals = []

    for i, turn in enumerate(turns):
        if turn["role"] != "user":
            continue

        content = turn["content"]
        for pattern in DOMAIN_SIGNAL_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                signals.append({
                    "turn_index": i,
                    "content": content,
                    "signal_type": "domain_explicit",
                    "signal_strength": "medium",
                })
                break  # One match per turn is enough

    return signals


def score_session_value(turns: list[dict]) -> dict:
    """Score a session's potential knowledge value.
    
    Returns:
        {
            "score": float (0-1),
            "corrections": int,
            "domain_signals": int,
            "turn_count": int,
            "should_extract": bool,
            "reason": str,
        }
    """
    corrections = detect_corrections(turns)
    domain_signals = detect_domain_signals(turns)
    turn_count = len(turns)

    # Scoring
    correction_score = min(len(corrections) * 0.3, 1.0)
    domain_score = min(len(domain_signals) * 0.2, 0.6)
    length_score = min(turn_count / 30, 0.4)  # Longer sessions may have more knowledge

    total_score = min(correction_score + domain_score + length_score, 1.0)

    # Determine if extraction is worthwhile
    should_extract = total_score >= 0.2 or len(corrections) >= 1
    reason = _build_reason(corrections, domain_signals, turn_count, total_score)

    return {
        "score": round(total_score, 3),
        "corrections": len(corrections),
        "domain_signals": len(domain_signals),
        "turn_count": turn_count,
        "should_extract": should_extract,
        "reason": reason,
        "correction_details": corrections,
        "signal_details": domain_signals,
    }


def _correction_strength(patterns: list[str]) -> str:
    if len(patterns) >= 3:
        return "high"
    if len(patterns) >= 2:
        return "medium"
    return "low"


def _build_reason(corrections, domain_signals, turn_count, score) -> str:
    parts = []
    if corrections:
        parts.append(f"{len(corrections)} correction(s) detected")
    if domain_signals:
        parts.append(f"{len(domain_signals)} domain signal(s)")
    if turn_count >= 20:
        parts.append(f"long session ({turn_count} turns)")
    if not parts:
        parts.append(f"low signal (score={score:.2f})")
    return "; ".join(parts)
