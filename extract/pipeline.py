"""Main extraction pipeline: Session JSONL → Structured knowledge.

Optimizations:
- Prompt caching (cache_control: ephemeral) on static system prompts.
- Message Batches API for Sonnet structuring (`--batch`, ~50% cheaper).
- Async parallel Haiku filter across sessions (`--parallel N`).
- try/finally guard prevents duplicate token spend on retry.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Load ANTHROPIC_API_KEY (and friends) from a local .env if present."""
    seen: set[Path] = set()
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        candidate = base / ".env"
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            pass


_load_dotenv()

import anthropic
from rich.console import Console
from rich.table import Table

# Resolve imports whether run as module or script
sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (
    get_db, get_all_knowledge, get_pending_sessions,
    mark_session_extracted, save_knowledge, log_extraction_cost
)
from extract.parser import parse_session, turns_to_conversation_text
from extract.signals import score_session_value
from extract.dedup import is_duplicate, find_related

console = Console()

PROMPTS_DIR = Path(__file__).parent / "prompts"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

# Haiku's 200k-token context maps to roughly ~800k characters. Cap filter
# input well below that so the prompt + system overhead + output still fit.
FILTER_INPUT_CHAR_BUDGET = 600_000


# ─────────────────────────────────────────────────────────────────────────────
# Prompt loading
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_CACHE: dict[str, tuple[str, str]] = {}


def load_prompt(name: str) -> str:
    """Legacy single-string loader (system + user concatenated)."""
    return (PROMPTS_DIR / f"{name}.xml").read_text(encoding="utf-8")


def split_prompt(name: str) -> tuple[str, str]:
    """Return (system_text, user_template) from an .xml prompt file.

    Supports <system>...</system> and <user>...</user> tags. The system
    block is fully static — safe to cache. The user block holds
    placeholders like {transcript}.
    """
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]

    raw = load_prompt(name)
    sys_match = re.search(r"<system>(.*?)</system>", raw, re.DOTALL)
    usr_match = re.search(r"<user>(.*?)</user>", raw, re.DOTALL)
    if not sys_match or not usr_match:
        raise ValueError(f"Prompt {name}.xml missing <system> or <user> block")
    system_text = sys_match.group(1).strip()
    user_template = usr_match.group(1).strip()
    _PROMPT_CACHE[name] = (system_text, user_template)
    return system_text, user_template


def _parse_json_payload(text: str) -> dict:
    """Strip markdown fences and parse JSON from a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


# ─────────────────────────────────────────────────────────────────────────────
# Backend: Anthropic API (with prompt caching)
# ─────────────────────────────────────────────────────────────────────────────

def _cached_system(system_text: str) -> list[dict]:
    """Build a system block with ephemeral cache_control."""
    return [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]


def _usage_dict(usage) -> dict:
    """Extract usage + cache stats from an Anthropic response."""
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def _truncate_for_filter(transcript_text: str) -> str:
    """Trim oversized transcripts to fit Haiku's context window.

    Drops the oldest turns and prepends a marker, preserving the tail where
    corrections and conclusions are most likely to live.
    """
    if len(transcript_text) <= FILTER_INPUT_CHAR_BUDGET:
        return transcript_text
    dropped = len(transcript_text) - FILTER_INPUT_CHAR_BUDGET
    return (f"[... truncated {dropped} earlier characters to fit context ...]\n\n"
            + transcript_text[-FILTER_INPUT_CHAR_BUDGET:])


def run_filter_api(client: anthropic.Anthropic, transcript_text: str,
                   model: str = HAIKU_MODEL) -> dict:
    """Stage 1: Haiku filters for knowledge candidates (cached system)."""
    transcript_text = _truncate_for_filter(transcript_text)
    system_text, user_template = split_prompt("filter")
    user_content = user_template.replace("{transcript}", transcript_text)

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=_cached_system(system_text),
        messages=[{"role": "user", "content": user_content}],
    )
    result = _parse_json_payload(response.content[0].text)
    result["_usage"] = _usage_dict(response.usage)
    return result


async def run_filter_api_async(client: anthropic.AsyncAnthropic,
                               transcript_text: str,
                               model: str = HAIKU_MODEL) -> dict:
    transcript_text = _truncate_for_filter(transcript_text)
    system_text, user_template = split_prompt("filter")
    user_content = user_template.replace("{transcript}", transcript_text)
    response = await client.messages.create(
        model=model,
        max_tokens=2000,
        system=_cached_system(system_text),
        messages=[{"role": "user", "content": user_content}],
    )
    result = _parse_json_payload(response.content[0].text)
    result["_usage"] = _usage_dict(response.usage)
    return result


def _build_structure_user(candidate: dict, excerpt: str,
                          existing_knowledge: list[dict]) -> str:
    _, user_template = split_prompt("structure")
    existing_summary = ""
    if existing_knowledge:
        lines = [f"- [{k['id']}] {k['title']}: {k['content'][:100]}"
                 for k in existing_knowledge[:10]]
        existing_summary = "\n".join(lines)
    return (user_template
            .replace("{candidate_type}", candidate["type"])
            .replace("{candidate_signal}", candidate["signal"])
            .replace("{candidate_brief}", candidate["brief"])
            .replace("{excerpt}", excerpt)
            .replace("{existing_knowledge}", existing_summary or "(none)"))


def run_structure_api(client: anthropic.Anthropic, candidate: dict,
                      excerpt: str, existing_knowledge: list[dict],
                      model: str = SONNET_MODEL) -> dict:
    """Stage 2: Sonnet structures a candidate (cached system)."""
    system_text, _ = split_prompt("structure")
    user_content = _build_structure_user(candidate, excerpt, existing_knowledge)

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=_cached_system(system_text),
        messages=[{"role": "user", "content": user_content}],
    )
    result = _parse_json_payload(response.content[0].text)
    result["_usage"] = _usage_dict(response.usage)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Backend dispatch
# ─────────────────────────────────────────────────────────────────────────────

def run_filter(client, transcript_text: str, model: str = HAIKU_MODEL) -> dict:
    return run_filter_api(client, transcript_text, model)


def run_structure(client, candidate: dict, excerpt: str,
                  existing_knowledge: list[dict],
                  model: str = SONNET_MODEL) -> dict:
    return run_structure_api(client, candidate, excerpt, existing_knowledge, model)


# ─────────────────────────────────────────────────────────────────────────────
# Message Batches (Sonnet structuring — 50% off, async completion)
# ─────────────────────────────────────────────────────────────────────────────

def run_structure_batch(client: anthropic.Anthropic,
                        jobs: list[dict],
                        model: str = SONNET_MODEL,
                        poll_interval: int = 15) -> dict[str, dict]:
    """Submit structuring jobs via Message Batches API.

    jobs: [{"custom_id": str, "candidate": dict, "excerpt": str, "existing": list}, ...]
    Returns: {custom_id: parsed_result_dict_with__usage}
    """
    from anthropic.types.messages.batch_create_params import Request
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    system_text, _ = split_prompt("structure")
    requests = []
    for job in jobs:
        user_content = _build_structure_user(
            job["candidate"], job["excerpt"], job["existing"])
        requests.append(Request(
            custom_id=job["custom_id"],
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=1500,
                system=_cached_system(system_text),
                messages=[{"role": "user", "content": user_content}],
            ),
        ))

    console.print(f"[blue]Batches: submitting {len(requests)} structuring jobs (50% off)...[/blue]")
    batch = client.messages.batches.create(requests=requests)
    console.print(f"[dim]Batch id: {batch.id}[/dim]")

    # Poll until done
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        console.print(
            f"[dim]Batch status: {batch.processing_status} "
            f"(processing={counts.processing} succeeded={counts.succeeded} "
            f"errored={counts.errored})[/dim]"
        )
        time.sleep(poll_interval)

    # Retrieve results
    results: dict[str, dict] = {}
    for entry in client.messages.batches.results(batch.id):
        cid = entry.custom_id
        if entry.result.type != "succeeded":
            console.print(f"[red]Batch job {cid} failed: {entry.result.type}[/red]")
            continue
        msg = entry.result.message
        try:
            parsed = _parse_json_payload(msg.content[0].text)
        except Exception as e:
            console.print(f"[red]Parse failed for {cid}: {e}[/red]")
            continue
        parsed["_usage"] = _usage_dict(msg.usage)
        results[cid] = parsed
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Per-session extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_session(session_path: str, session_id: str,
                    dry_run: bool = False,
                    use_batch: bool = False) -> list[dict]:
    """Full extraction pipeline for a single session.

    Re-extraction guard: session is marked `extracted = 1` in a try/finally,
    so a crash mid-pipeline will not cause double token spend on retry.
    """
    db = get_db()
    client = anthropic.Anthropic()
    extracted: list[dict] = []

    try:
        # 1. Parse transcript
        console.print(f"[dim]Parsing: {session_path}[/dim]")
        session_data = parse_session(session_path)

        if session_data["turn_count"] < 5:
            console.print("[yellow]Skipped: too few turns[/yellow]")
            return []

        # 2. Score session value (rule-based, free)
        value = score_session_value(session_data["turns"])
        console.print(f"[dim]Session score: {value['score']} "
                      f"({value['reason']})[/dim]")

        if not value["should_extract"]:
            console.print("[yellow]Skipped: low signal[/yellow]")
            return []

        # 3. Convert to text for LLM
        transcript_text = turns_to_conversation_text(session_data["turns"])

        # 4. Stage 1: Haiku filter
        console.print("[blue]Stage 1: Filtering (Haiku)...[/blue]")
        if dry_run:
            console.print("[dim]Dry run — skipping LLM calls[/dim]")
            return []

        try:
            filter_result = run_filter(client, transcript_text)
        except (json.JSONDecodeError, anthropic.APIError, RuntimeError) as e:
            console.print(f"[red]Filter failed: {e}[/red]")
            return []

        haiku_usage = filter_result.get("_usage", {})
        haiku_cost = _estimate_cost("haiku", haiku_usage)
        log_extraction_cost(
            db, session_id, "filter", "haiku",
            haiku_usage.get("input_tokens", 0),
            haiku_usage.get("output_tokens", 0),
            haiku_cost,
            cache_read_input_tokens=haiku_usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=haiku_usage.get("cache_creation_input_tokens", 0),
        )
        _log_cache_stats("filter", haiku_usage)

        if not filter_result.get("has_knowledge"):
            console.print("[yellow]No knowledge candidates found[/yellow]")
            return []

        candidates = filter_result.get("candidates", [])
        console.print(f"[green]Found {len(candidates)} candidate(s)[/green]")

        # 5. Stage 2: Sonnet structure
        existing = get_all_knowledge(db)

        if use_batch and len(candidates) > 0:
            extracted = _structure_and_save_batch(
                db, client, session_id, session_data, candidates, existing)
        else:
            extracted = _structure_and_save_sync(
                db, client, session_id, session_data, candidates, existing)

        return extracted
    finally:
        mark_session_extracted(db, session_id)
        db.close()


def _structure_and_save_sync(db, client, session_id, session_data,
                             candidates, existing) -> list[dict]:
    extracted: list[dict] = []
    for i, candidate in enumerate(candidates):
        console.print(
            f"[blue]Stage 2: Structuring candidate {i+1}/{len(candidates)} "
            f"(Sonnet)...[/blue]"
        )
        start, end = candidate.get("turn_range", [0, len(session_data["turns"])])
        excerpt_turns = session_data["turns"][start:end+1]
        excerpt = turns_to_conversation_text(excerpt_turns, max_tokens=3000)

        try:
            knowledge = run_structure(client, candidate, excerpt, existing)
        except (json.JSONDecodeError, anthropic.APIError, RuntimeError) as e:
            console.print(f"[red]Structure failed: {e}[/red]")
            continue

        _finalize_knowledge(db, session_id, knowledge, existing, extracted)
    return extracted


def _structure_and_save_batch(db, client, session_id, session_data,
                              candidates, existing) -> list[dict]:
    """Submit all candidates to Batches API, then dedup+save sequentially."""
    jobs = []
    for i, candidate in enumerate(candidates):
        start, end = candidate.get("turn_range", [0, len(session_data["turns"])])
        excerpt_turns = session_data["turns"][start:end+1]
        excerpt = turns_to_conversation_text(excerpt_turns, max_tokens=3000)
        jobs.append({
            "custom_id": f"{session_id[:8]}-cand-{i}",
            "candidate": candidate,
            "excerpt": excerpt,
            "existing": existing,
        })

    results = run_structure_batch(client, jobs)

    extracted: list[dict] = []
    # Preserve candidate order so dedup sees items deterministically
    for job in jobs:
        knowledge = results.get(job["custom_id"])
        if not knowledge:
            continue
        _finalize_knowledge(db, session_id, knowledge, existing, extracted,
                            is_batch=True)
    return extracted


def _finalize_knowledge(db, session_id, knowledge, existing, extracted,
                        is_batch: bool = False) -> None:
    """Log cost, dedup, find related, save, append to lists."""
    sonnet_usage = knowledge.get("_usage", {})
    sonnet_cost = _estimate_cost("sonnet", sonnet_usage, is_batch=is_batch)
    log_extraction_cost(
        db, session_id, "structure", "sonnet",
        sonnet_usage.get("input_tokens", 0),
        sonnet_usage.get("output_tokens", 0),
        sonnet_cost,
        cache_read_input_tokens=sonnet_usage.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=sonnet_usage.get("cache_creation_input_tokens", 0),
    )
    _log_cache_stats("structure", sonnet_usage)

    knowledge.pop("_usage", None)
    knowledge.pop("practical_insight", None)

    existing_contents = [k["content"] for k in existing]
    is_dup, sim = is_duplicate(knowledge["content"], existing_contents)
    if is_dup:
        console.print(
            f"[yellow]Duplicate (sim={sim}): {knowledge['title']}[/yellow]"
        )
        return

    related = find_related(knowledge["content"], existing)
    knowledge["related_ids"] = related
    knowledge["source_session_id"] = session_id

    if knowledge.get("confidence", 0) >= 0.9:
        knowledge["approved"] = 1

    save_knowledge(db, knowledge)
    extracted.append(knowledge)
    existing.append(knowledge)
    console.print(
        f"[green]✓ Extracted: {knowledge['title']} "
        f"(confidence={knowledge.get('confidence', '?')})[/green]"
    )


def _log_cache_stats(stage: str, usage: dict) -> None:
    cr = usage.get("cache_read_input_tokens", 0)
    cw = usage.get("cache_creation_input_tokens", 0)
    if cr or cw:
        console.print(f"[dim]{stage} cache: read={cr} write={cw}[/dim]")


# ─────────────────────────────────────────────────────────────────────────────
# Batch driver over multiple sessions
# ─────────────────────────────────────────────────────────────────────────────

async def _prefilter_sessions_async(pending: list[dict]) -> dict[str, dict]:
    """Run Haiku filter in parallel across multiple sessions.

    Returns {session_id: filter_result_or_None}. Sessions that fail
    filtering get None and will be retried sequentially.
    """
    client = anthropic.AsyncAnthropic()
    results: dict[str, dict] = {}

    async def _one(session):
        sid = session["id"]
        try:
            session_data = parse_session(session["transcript_path"])
            if session_data["turn_count"] < 5:
                return sid, {"_skip": "few_turns"}
            value = score_session_value(session_data["turns"])
            if not value["should_extract"]:
                return sid, {"_skip": "low_signal"}
            transcript_text = turns_to_conversation_text(session_data["turns"])
            fr = await run_filter_api_async(client, transcript_text)
            fr["_session_data"] = session_data
            return sid, fr
        except Exception as e:
            console.print(f"[red]Prefilter failed for {sid[:8]}: {e}[/red]")
            return sid, None

    tasks = [_one(s) for s in pending]
    for coro in asyncio.as_completed(tasks):
        sid, fr = await coro
        results[sid] = fr
    await client.close()
    return results


def extract_pending(dry_run: bool = False, project_path: str | None = None,
                    use_batch: bool = False, parallel: int = 1):
    """Extract all pending sessions, optionally scoped to one project."""
    db = get_db()
    pending = get_pending_sessions(db, project_path=project_path)
    db.close()

    if not pending:
        scope = f" for {project_path}" if project_path else ""
        console.print(f"[dim]No pending sessions{scope}.[/dim]")
        return

    scope = f" (project: {project_path})" if project_path else ""
    flags = []
    if use_batch:
        flags.append("batch")
    if parallel > 1:
        flags.append(f"parallel={parallel}")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    console.print(
        f"[bold]Processing {len(pending)} pending session(s){scope}{flag_str}...[/bold]"
    )

    # Optional: async-parallel prefilter across sessions
    prefiltered: dict[str, dict] = {}
    if parallel > 1 and not dry_run:
        console.print(f"[blue]Prefilter: parallel Haiku across {len(pending)} sessions[/blue]")
        prefiltered = asyncio.run(_prefilter_sessions_async(pending))

    total_extracted = 0
    for session in pending:
        console.print(f"\n[bold]Session: {session['id'][:12]}...[/bold]")
        try:
            if session["id"] in prefiltered and prefiltered[session["id"]] is not None:
                results = _extract_with_prefilter(
                    session, prefiltered[session["id"]], use_batch=use_batch)
            else:
                results = extract_session(
                    session["transcript_path"],
                    session["id"],
                    dry_run=dry_run,
                    use_batch=use_batch,
                )
            total_extracted += len(results)
        except Exception as e:
            console.print(f"[red]Session {session['id'][:8]} crashed: {e}[/red]")
            # Guard already marked it extracted in the finally block.
            continue

    console.print(
        f"\n[bold green]Done. Extracted {total_extracted} knowledge item(s).[/bold green]"
    )


def _extract_with_prefilter(session: dict, filter_result: dict,
                            use_batch: bool) -> list[dict]:
    """Finish extraction using a pre-computed filter result.

    Skips re-running Haiku. Still runs structure + dedup + save under
    the try/finally guard.
    """
    session_id = session["id"]
    db = get_db()
    client = anthropic.Anthropic()
    extracted: list[dict] = []
    try:
        if "_skip" in filter_result:
            return []
        session_data = filter_result.pop("_session_data", None)
        if session_data is None:
            session_data = parse_session(session["transcript_path"])

        haiku_usage = filter_result.get("_usage", {})
        haiku_cost = _estimate_cost("haiku", haiku_usage)
        log_extraction_cost(
            db, session_id, "filter", "haiku",
            haiku_usage.get("input_tokens", 0),
            haiku_usage.get("output_tokens", 0),
            haiku_cost,
            cache_read_input_tokens=haiku_usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=haiku_usage.get("cache_creation_input_tokens", 0),
        )
        _log_cache_stats("filter", haiku_usage)

        if not filter_result.get("has_knowledge"):
            return []

        candidates = filter_result.get("candidates", [])
        console.print(f"[green]Found {len(candidates)} candidate(s) (prefiltered)[/green]")

        existing = get_all_knowledge(db)
        if use_batch and len(candidates) > 0:
            extracted = _structure_and_save_batch(
                db, client, session_id, session_data, candidates, existing)
        else:
            extracted = _structure_and_save_sync(
                db, client, session_id, session_data, candidates, existing)
        return extracted
    finally:
        mark_session_extracted(db, session_id)
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Cost estimation
# ─────────────────────────────────────────────────────────────────────────────

def _estimate_cost(model: str, usage: dict, is_batch: bool = False) -> float:
    """Estimate API cost in USD including cache discounts.

    Cache reads: 10% of base input price.
    Cache writes (ephemeral, 5-min TTL): 125% of base input price.
    Batch: 50% off everything (applied here when is_batch=True).
    """
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)

    if "haiku" in model:
        in_price, out_price = 0.25, 1.25
    elif "sonnet" in model:
        in_price, out_price = 3.0, 15.0
    else:
        in_price, out_price = 15.0, 75.0

    base = (inp * in_price + out * out_price) / 1_000_000
    cache_bonus = (cache_read * in_price * 0.1
                   + cache_write * in_price * 0.25) / 1_000_000
    total = base + cache_bonus
    if is_batch:
        total *= 0.5
    return total


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Mojo knowledge extraction")
    parser.add_argument("--session", help="Extract specific session by path")
    parser.add_argument("--session-id", help="Session ID (used with --session)")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    parser.add_argument("--batch", action="store_true",
                        help="Use Message Batches API for Sonnet structuring "
                             "(~50%% cheaper, async — may take minutes to hours)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Parallel Haiku filter across N sessions (api backend only)")
    parser.add_argument(
        "--project", "-p",
        help="Only extract sessions belonging to this project path "
             "(defaults to the current working directory). Use '--project all' "
             "to process every pending session across projects.",
    )
    args = parser.parse_args()

    if args.session:
        sid = args.session_id or Path(args.session).stem
        extract_session(args.session, sid, dry_run=args.dry_run,
                        use_batch=args.batch)
        return

    if args.project and args.project.lower() == "all":
        project_path: str | None = None
    else:
        project_path = args.project or str(Path.cwd())
    extract_pending(dry_run=args.dry_run, project_path=project_path,
                    use_batch=args.batch, parallel=args.parallel)


if __name__ == "__main__":
    main()
