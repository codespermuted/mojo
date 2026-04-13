"""Token-budget-aware knowledge packing for CLAUDE.md injection."""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import evidence_based_grade  # noqa: E402


# Per-grade weight used in the value score. Mirrors the A-F hierarchy
# without collapsing into raw confidence thresholds.
GRADE_WEIGHT = {
    "A": 1.00,
    "B": 0.80,
    "C": 0.60,
    "D": 0.35,
    "F": 0.00,
}


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~3.5 chars per token for Korean+English mix."""
    return max(1, math.ceil(len(text) / 3.5))


def recency_score(updated_at: str) -> float:
    """Score based on how recently the knowledge was updated. 0-1."""
    if not updated_at:
        return 0.5
    try:
        updated = datetime.fromisoformat(updated_at)
        age_days = (datetime.now() - updated).days
        # Decay over 180 days
        return max(0.0, 1.0 - (age_days / 180))
    except (ValueError, TypeError):
        return 0.5


def knowledge_value_score(item: dict, domain_priorities: dict = None) -> float:
    """Calculate value score for a knowledge item.

    Higher score = higher priority for inclusion in token budget.
    Uses the evidence-based grade: verified / corroborated items rank
    above unvalidated auto-extracted noise.
    """
    grade = evidence_based_grade(item)
    grade_weight = GRADE_WEIGHT.get(grade, 0.5)
    usage = min(item.get("usage_count", 0) / 10.0, 1.0)
    approved = 1.0 if item.get("approved") else 0.6
    recency = recency_score(item.get("updated_at", ""))

    # Domain priority boost
    domain_boost = 1.0
    if domain_priorities:
        domain = item.get("domain", "")
        for prefix, priority in domain_priorities.items():
            if domain.startswith(prefix):
                domain_boost = priority
                break

    # Type weights: anti-patterns and domain rules are most valuable
    type_weights = {
        "anti_pattern": 1.2,
        "domain_rule": 1.1,
        "architecture_decision": 1.0,
        "debug_playbook": 0.9,
        "code_pattern": 0.85,
        "tool_preference": 0.8,
    }
    type_weight = type_weights.get(item.get("type", ""), 0.8)

    score = (
        grade_weight * 0.35 +
        usage * 0.20 +
        approved * 0.15 +
        recency * 0.15 +
        type_weight * 0.15
    ) * domain_boost

    return round(score, 4)


def pack_knowledge(items: list[dict], token_budget: int,
                   domain_priorities: dict = None) -> list[dict]:
    """Greedy knapsack: pack highest-value items within token budget.
    
    Returns list of items that fit within the budget, sorted by domain.
    """
    # Score and estimate tokens for each item
    scored = []
    for item in items:
        content = item.get("content", "")
        reasoning = item.get("reasoning", "")
        title = item.get("title", "")
        # Estimate tokens for the rendered version (title + content + 1-line reasoning)
        rendered = f"{title}\n{content}\n{reasoning[:100]}"
        tokens = estimate_tokens(rendered) + 10  # overhead for formatting
        value = knowledge_value_score(item, domain_priorities)

        scored.append({
            **item,
            "_tokens": tokens,
            "_value": value,
            "_efficiency": value / max(tokens ** 0.5, 1),  # sqrt dampens token penalty for detailed items
        })

    # Sort by efficiency (value/token)
    scored.sort(key=lambda x: x["_efficiency"], reverse=True)

    # Greedy pack
    packed = []
    used_tokens = 0

    for item in scored:
        if used_tokens + item["_tokens"] <= token_budget:
            packed.append(item)
            used_tokens += item["_tokens"]

    # Sort packed items by domain for readable output
    packed.sort(key=lambda x: (x.get("domain", ""), x.get("type", "")))

    # Clean internal fields
    for item in packed:
        item.pop("_tokens", None)
        item.pop("_value", None)
        item.pop("_efficiency", None)

    return packed
