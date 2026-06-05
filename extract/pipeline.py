"""Main extraction pipeline: Session JSONL → Structured knowledge.

LLM calls go through a pluggable backend (extract/llm_backend.py):

- claude-cli (default): headless `claude -p`, zero API cost (subscription)
- codex-cli: headless `codex exec`, zero API cost (subscription)
- api: Anthropic API with prompt caching; supports --batch (Message
  Batches, ~50% off Sonnet structuring) and cost tracking.

Other optimizations:
- Parallel filter stage across sessions (`--parallel N`, thread-based,
  works with every backend).
- try/finally guard prevents duplicate token spend on retry.
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from rich.console import Console

# Resolve imports whether run as module or script
sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (
    get_db, get_all_knowledge, get_pending_sessions,
    init_db, mark_session_extracted, save_knowledge, log_extraction_cost,
    record_observation,
)
from mojo_config import load_config
from extract.llm_backend import (
    BackendError, LLMBackend, ROLE_FILTER, ROLE_STRUCTURE, get_backend,
)
from extract.parser import parse_session, turns_to_conversation_text
from extract.signals import score_session_value
from extract.dedup import find_duplicate, find_related

console = Console()

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Filter-stage context maps to roughly ~800k characters at 200k tokens.
# Cap filter input well below that so prompt + system overhead + output fit.
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
    # CLI backends sometimes wrap JSON in prose; recover the outermost
    # object if direct parsing fails.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ─────────────────────────────────────────────────────────────────────────────
# LLM stages (backend-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

_RELATION_PROMPT = (
    "You connect two pieces of domain knowledge that a similarity search "
    "flagged as related. Explain, in ONE sentence (max 140 chars), what "
    "they share in substance — the principle, constraint, or workflow step "
    "that makes them meaningful companions. Do not just say they are "
    "'similar' or 'about the same topic'. No preamble, no quotes, no "
    "trailing period if the sentence ends in a noun. Output only the "
    "sentence."
)


def _explain_relation(backend: Optional[LLMBackend], source: dict,
                      target: dict) -> str:
    """One-sentence explanation of why two knowledge items are related.

    Called after the TF-IDF `find_related` picks a neighbor so the edge
    carries human-readable context instead of a bare ID link.
    """
    if backend is None:
        return ""
    try:
        user_content = (
            f"Item A — {source.get('type', '')} · {source.get('domain', '')}\n"
            f"Title: {source.get('title', '')}\n"
            f"Content: {(source.get('content') or '')[:400]}\n\n"
            f"Item B — {target.get('type', '')} · {target.get('domain', '')}\n"
            f"Title: {target.get('title', '')}\n"
            f"Content: {(target.get('content') or '')[:400]}"
        )
        result = backend.complete(_RELATION_PROMPT, user_content,
                                  max_tokens=80, role=ROLE_FILTER)
        return result["text"].strip()[:160]
    except (BackendError, KeyError) as e:
        console.print(f"[dim]relation-reason failed: {e}[/dim]")
        return ""


def _truncate_for_filter(transcript_text: str) -> str:
    """Trim oversized transcripts to fit the filter model's context window.

    Drops the oldest turns and prepends a marker, preserving the tail where
    corrections and conclusions are most likely to live.
    """
    if len(transcript_text) <= FILTER_INPUT_CHAR_BUDGET:
        return transcript_text
    dropped = len(transcript_text) - FILTER_INPUT_CHAR_BUDGET
    return (f"[... truncated {dropped} earlier characters to fit context ...]\n\n"
            + transcript_text[-FILTER_INPUT_CHAR_BUDGET:])


def run_filter(backend: LLMBackend, transcript_text: str) -> dict:
    """Stage 1: cheap model filters for knowledge candidates."""
    transcript_text = _truncate_for_filter(transcript_text)
    system_text, user_template = split_prompt("filter")
    user_content = user_template.replace("{transcript}", transcript_text)

    result = backend.complete(system_text, user_content,
                              max_tokens=2000, role=ROLE_FILTER)
    parsed = _parse_json_payload(result["text"])
    parsed["_usage"] = result["usage"]
    parsed["_cost_usd"] = result["cost_usd"]
    parsed["_model"] = result["model"]
    return parsed


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


def run_structure(backend: LLMBackend, candidate: dict, excerpt: str,
                  existing_knowledge: list[dict]) -> dict:
    """Stage 2: strong model structures a candidate."""
    system_text, _ = split_prompt("structure")
    user_content = _build_structure_user(candidate, excerpt, existing_knowledge)

    result = backend.complete(system_text, user_content,
                              max_tokens=1500, role=ROLE_STRUCTURE)
    parsed = _parse_json_payload(result["text"])
    parsed["_usage"] = result["usage"]
    parsed["_cost_usd"] = result["cost_usd"]
    parsed["_model"] = result["model"]
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Message Batches (api backend only — 50% off, async completion)
# ─────────────────────────────────────────────────────────────────────────────

def run_structure_batch(backend, jobs: list[dict],
                        poll_interval: int = 15) -> dict[str, dict]:
    """Submit structuring jobs via Message Batches API (api backend only).

    jobs: [{"custom_id": str, "candidate": dict, "excerpt": str, "existing": list}, ...]
    Returns: {custom_id: parsed_result_dict_with__usage}
    """
    from anthropic.types.messages.batch_create_params import Request
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from extract.llm_backend import _api_cost

    client = backend.client
    model = backend.model_for(ROLE_STRUCTURE)
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
                system=[{
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }],
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
        usage = msg.usage
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        parsed["_usage"] = usage_dict
        parsed["_cost_usd"] = _api_cost(model, usage_dict) * 0.5  # batch discount
        parsed["_model"] = model
        results[cid] = parsed
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Per-session extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_session(session_path: str, session_id: str,
                    dry_run: bool = False,
                    use_batch: bool = False,
                    backend: Optional[LLMBackend] = None) -> list[dict]:
    """Full extraction pipeline for a single session.

    Re-extraction guard: session is marked `extracted = 1` in a try/finally,
    so a crash mid-pipeline will not cause double token spend on retry.
    """
    init_db()
    db = get_db()
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

        # 4. Stage 1: filter
        if dry_run:
            console.print("[dim]Dry run — skipping LLM calls[/dim]")
            return []

        if backend is None:
            backend = get_backend(config=load_config())
        use_batch = _validate_batch_flag(use_batch, backend)

        console.print(f"[blue]Stage 1: Filtering ({backend.model_for(ROLE_FILTER)})...[/blue]")
        try:
            filter_result = run_filter(backend, transcript_text)
        except (json.JSONDecodeError, BackendError) as e:
            console.print(f"[red]Filter failed: {e}[/red]")
            return []

        _log_stage_cost(db, session_id, "filter", filter_result)

        if not filter_result.get("has_knowledge"):
            console.print("[yellow]No knowledge candidates found[/yellow]")
            return []

        candidates = filter_result.get("candidates", [])
        console.print(f"[green]Found {len(candidates)} candidate(s)[/green]")

        # 5. Stage 2: structure
        existing = get_all_knowledge(db)

        if use_batch and len(candidates) > 0:
            extracted = _structure_and_save_batch(
                db, backend, session_id, session_data, candidates, existing)
        else:
            extracted = _structure_and_save_sync(
                db, backend, session_id, session_data, candidates, existing)

        return extracted
    finally:
        mark_session_extracted(db, session_id)
        db.close()


def _validate_batch_flag(use_batch: bool, backend: LLMBackend) -> bool:
    if use_batch and backend.name != "api":
        console.print(
            "[yellow]--batch needs the api backend (Message Batches); "
            f"running synchronously on {backend.name}.[/yellow]"
        )
        return False
    return use_batch


def _structure_and_save_sync(db, backend, session_id, session_data,
                             candidates, existing) -> list[dict]:
    extracted: list[dict] = []
    for i, candidate in enumerate(candidates):
        console.print(
            f"[blue]Stage 2: Structuring candidate {i+1}/{len(candidates)} "
            f"({backend.model_for(ROLE_STRUCTURE)})...[/blue]"
        )
        start, end = candidate.get("turn_range", [0, len(session_data["turns"])])
        excerpt_turns = session_data["turns"][start:end+1]
        excerpt = turns_to_conversation_text(excerpt_turns, max_tokens=3000)

        try:
            knowledge = run_structure(backend, candidate, excerpt, existing)
        except (json.JSONDecodeError, BackendError) as e:
            console.print(f"[red]Structure failed: {e}[/red]")
            continue

        _finalize_knowledge(
            db, backend, session_id, knowledge, existing, extracted,
            project_path=session_data.get("project_path", ""),
            evidence_excerpt=excerpt,
        )
    return extracted


def _structure_and_save_batch(db, backend, session_id, session_data,
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

    results = run_structure_batch(backend, jobs)

    extracted: list[dict] = []
    # Preserve candidate order so dedup sees items deterministically
    for job in jobs:
        knowledge = results.get(job["custom_id"])
        if not knowledge:
            continue
        _finalize_knowledge(
            db, backend, session_id, knowledge, existing, extracted,
            project_path=session_data.get("project_path", ""),
            evidence_excerpt=job["excerpt"],
        )
    return extracted


def _finalize_knowledge(db, backend, session_id, knowledge, existing, extracted,
                        project_path: str = "",
                        evidence_excerpt: str = "") -> None:
    """Log cost, dedup-or-observe, find related, save, append to lists.

    Re-observation is evidence, not noise: when the same claim already
    exists, record an observation on the existing item instead of
    discarding the extraction. Observations from *other* projects are what
    later justify generalizing the item's scope.
    """
    _log_stage_cost(db, session_id, "structure", knowledge)

    knowledge.pop("_usage", None)
    knowledge.pop("_cost_usd", None)
    knowledge.pop("_model", None)
    knowledge.pop("practical_insight", None)
    suggested_state = knowledge.pop("suggested_promotion_state", None)
    human_review_required = knowledge.pop("human_review_required", None)

    dup_of, sim = find_duplicate(knowledge["content"], existing)
    if dup_of is not None:
        console.print(
            f"[cyan]Re-observation (sim={sim}) of [{dup_of['id']}] "
            f"{dup_of['title']} — recording evidence[/cyan]"
        )
        record_observation(
            db, dup_of["id"],
            project_path=project_path,
            session_id=session_id,
            stance="supports",
            note=f"Re-extracted as: {knowledge.get('title', '')}",
        )
        return

    related_pairs = find_related(knowledge["content"], existing)
    knowledge["related_ids"] = [rid for rid, _ in related_pairs]
    knowledge["related_scores"] = {rid: score for rid, score in related_pairs}
    if related_pairs:
        by_id = {it["id"]: it for it in existing}
        reasoning = {}
        for rid, _ in related_pairs:
            target = by_id.get(rid)
            if not target:
                continue
            reason = _explain_relation(backend, knowledge, target)
            if reason:
                reasoning[rid] = reason
        if reasoning:
            knowledge["related_reasoning"] = reasoning
    knowledge["source_session_id"] = session_id
    knowledge["project_path"] = project_path or knowledge.get("project_path")
    knowledge["source_lineage"] = {
        "kind": "session",
        "ref": session_id,
        "project_path": project_path,
    }
    knowledge["evidence_excerpt"] = evidence_excerpt[:4000] if evidence_excerpt else ""
    knowledge.setdefault("scope", "project" if project_path else "domain")
    knowledge.setdefault(
        "applies_when",
        f"When working in {project_path}." if project_path else "",
    )
    knowledge.setdefault(
        "does_not_apply_when",
        "Do not generalize outside the observed project/domain until reviewed.",
    )
    if knowledge.get("confidence", 0) >= 0.75 and knowledge.get("reasoning"):
        knowledge.setdefault("evidence_level", "local_rule")
    else:
        knowledge.setdefault("evidence_level", "raw_observation")
    knowledge.setdefault("promotion_state", suggested_state or "candidate")
    knowledge.setdefault("review_required", 1 if human_review_required is not False else 0)
    knowledge.setdefault("safe_to_generalize", 0)
    knowledge.setdefault("approved", 0)

    save_knowledge(db, knowledge)
    record_observation(
        db, knowledge["id"],
        project_path=project_path,
        session_id=session_id,
        stance="supports",
        note="initial extraction",
    )
    extracted.append(knowledge)
    existing.append(knowledge)
    console.print(
        f"[green]✓ Extracted: {knowledge['title']} "
        f"(confidence={knowledge.get('confidence', '?')})[/green]"
    )


def _log_stage_cost(db, session_id: str, stage: str, result: dict) -> None:
    usage = result.get("_usage", {})
    log_extraction_cost(
        db, session_id, stage, result.get("_model", "unknown"),
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        result.get("_cost_usd", 0.0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
    )
    cr = usage.get("cache_read_input_tokens", 0)
    cw = usage.get("cache_creation_input_tokens", 0)
    if cr or cw:
        console.print(f"[dim]{stage} cache: read={cr} write={cw}[/dim]")


# ─────────────────────────────────────────────────────────────────────────────
# Batch driver over multiple sessions
# ─────────────────────────────────────────────────────────────────────────────

def _prefilter_sessions(pending: list[dict], backend: LLMBackend,
                        workers: int) -> dict[str, dict]:
    """Run the filter stage in parallel threads across multiple sessions.

    Works with every backend (CLI subprocesses and API calls both block
    happily inside threads). Returns {session_id: filter_result_or_None};
    sessions that fail filtering get None and are retried sequentially.
    """
    results: dict[str, dict] = {}

    def _one(session):
        sid = session["id"]
        try:
            session_data = parse_session(session["transcript_path"])
            if session_data["turn_count"] < 5:
                return sid, {"_skip": "few_turns"}
            value = score_session_value(session_data["turns"])
            if not value["should_extract"]:
                return sid, {"_skip": "low_signal"}
            transcript_text = turns_to_conversation_text(session_data["turns"])
            fr = run_filter(backend, transcript_text)
            fr["_session_data"] = session_data
            return sid, fr
        except Exception as e:
            console.print(f"[red]Prefilter failed for {sid[:8]}: {e}[/red]")
            return sid, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, s) for s in pending]
        for fut in as_completed(futures):
            sid, fr = fut.result()
            results[sid] = fr
    return results


def extract_pending(dry_run: bool = False, project_path: str | None = None,
                    use_batch: bool = False, parallel: int = 1,
                    backend_name: str | None = None):
    """Extract all pending sessions, optionally scoped to one project."""
    init_db()
    db = get_db()
    pending = get_pending_sessions(db, project_path=project_path)
    db.close()

    if not pending:
        scope = f" for {project_path}" if project_path else ""
        console.print(f"[dim]No pending sessions{scope}.[/dim]")
        return

    backend = None
    if not dry_run:
        backend = get_backend(backend_name, config=load_config())
        use_batch = _validate_batch_flag(use_batch, backend)

    scope = f" (project: {project_path})" if project_path else ""
    flags = [f"backend={backend.name}"] if backend else []
    if use_batch:
        flags.append("batch")
    if parallel > 1:
        flags.append(f"parallel={parallel}")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    console.print(
        f"[bold]Processing {len(pending)} pending session(s){scope}{flag_str}...[/bold]"
    )

    # Optional: thread-parallel prefilter across sessions
    prefiltered: dict[str, dict] = {}
    if parallel > 1 and not dry_run:
        console.print(f"[blue]Prefilter: parallel filter across {len(pending)} sessions[/blue]")
        prefiltered = _prefilter_sessions(pending, backend, workers=parallel)

    total_extracted = 0
    for session in pending:
        console.print(f"\n[bold]Session: {session['id'][:12]}...[/bold]")
        try:
            if session["id"] in prefiltered and prefiltered[session["id"]] is not None:
                results = _extract_with_prefilter(
                    session, prefiltered[session["id"]], backend,
                    use_batch=use_batch)
            else:
                results = extract_session(
                    session["transcript_path"],
                    session["id"],
                    dry_run=dry_run,
                    use_batch=use_batch,
                    backend=backend,
                )
            total_extracted += len(results)
        except Exception as e:
            console.print(f"[red]Session {session['id'][:8]} crashed: {e}[/red]")
            # Guard already marked it extracted in the finally block.
            continue

    # Mirror new/updated knowledge into the Obsidian vault, if configured.
    if not dry_run:
        try:
            from serve.vault import export_vault
            export_vault(quiet=True)
        except Exception as e:
            console.print(f"[dim]vault export skipped: {e}[/dim]")

    console.print(
        f"\n[bold green]Done. Extracted {total_extracted} knowledge item(s).[/bold green]"
    )


def _extract_with_prefilter(session: dict, filter_result: dict,
                            backend: LLMBackend, use_batch: bool) -> list[dict]:
    """Finish extraction using a pre-computed filter result.

    Skips re-running the filter. Still runs structure + dedup + save under
    the try/finally guard.
    """
    session_id = session["id"]
    init_db()
    db = get_db()
    extracted: list[dict] = []
    try:
        if "_skip" in filter_result:
            return []
        session_data = filter_result.pop("_session_data", None)
        if session_data is None:
            session_data = parse_session(session["transcript_path"])

        _log_stage_cost(db, session_id, "filter", filter_result)

        if not filter_result.get("has_knowledge"):
            return []

        candidates = filter_result.get("candidates", [])
        console.print(f"[green]Found {len(candidates)} candidate(s) (prefiltered)[/green]")

        existing = get_all_knowledge(db)
        if use_batch and len(candidates) > 0:
            extracted = _structure_and_save_batch(
                db, backend, session_id, session_data, candidates, existing)
        else:
            extracted = _structure_and_save_sync(
                db, backend, session_id, session_data, candidates, existing)
        return extracted
    finally:
        mark_session_extracted(db, session_id)
        db.close()


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
    parser.add_argument("--backend", choices=["claude-cli", "codex-cli", "api"],
                        help="LLM backend (default: config extraction.backend, "
                             "then claude-cli — zero API cost)")
    parser.add_argument("--batch", action="store_true",
                        help="Use Message Batches API for structuring "
                             "(api backend only; ~50%% cheaper, async)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Parallel filter stage across N sessions")
    parser.add_argument(
        "--project", "-p",
        help="Only extract sessions belonging to this project path "
             "(defaults to the current working directory). Use '--project all' "
             "to process every pending session across projects.",
    )
    args = parser.parse_args()

    if args.session:
        sid = args.session_id or Path(args.session).stem
        backend = None
        if not args.dry_run:
            backend = get_backend(args.backend, config=load_config())
        extract_session(args.session, sid, dry_run=args.dry_run,
                        use_batch=args.batch, backend=backend)
        if not args.dry_run:
            try:
                from serve.vault import export_vault
                export_vault(quiet=True)
            except Exception as e:
                console.print(f"[dim]vault export skipped: {e}[/dim]")
        return

    if args.project and args.project.lower() == "all":
        project_path: str | None = None
    else:
        project_path = args.project or str(Path.cwd())
    extract_pending(dry_run=args.dry_run, project_path=project_path,
                    use_batch=args.batch, parallel=args.parallel,
                    backend_name=args.backend)


if __name__ == "__main__":
    main()
