"""Mojo web dashboard — FastAPI backend + static SPA.

Run: python dashboard/server.py
Opens http://localhost:8765 in the default browser.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (  # noqa: E402
    CONFIDENCE_GRADES,
    evidence_based_grade,
    get_db,
    get_details_for,
    get_stats,
    init_db,
    link_detail_to_summary,
    log_extraction_cost,
    save_knowledge,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Mojo Dashboard", version="0.1.0")


# ─── Schemas ─────────────────────────────────────────────────

class KnowledgeIn(BaseModel):
    id: Optional[str] = None
    type: str
    domain: str
    title: str
    content: str
    reasoning: Optional[str] = ""
    confidence: float = 0.85
    tags: list[str] = []
    related_ids: list[str] = []
    approved: int = 1
    source_session_id: str = "manual"
    status: str = "standalone"


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    approved: Optional[int] = None
    tags: Optional[list[str]] = None
    related_ids: Optional[list[str]] = None
    domain: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    parent_id: Optional[str] = None


class StructureRequest(BaseModel):
    detail_ids: list[str]


# ─── Helpers ─────────────────────────────────────────────────

def _build_lineage(item: dict) -> dict:
    """Describe where a knowledge item came from, for UI display."""
    source = item.get("source_session_id", "") or ""
    lineage: dict = {"source_type": "unknown", "detail": source or "unknown"}

    if source.startswith("memory-seed") or source.startswith("example"):
        lineage["source_type"] = "seed"
        lineage["detail"] = "Imported from seed file"
    elif source.startswith("git-scan-"):
        commit_hash = source.replace("git-scan-", "")
        lineage["source_type"] = "git"
        lineage["detail"] = f"Extracted from git commit {commit_hash}"
        lineage["commit_hash"] = commit_hash
    elif source.startswith("manual"):
        lineage["source_type"] = "manual"
        lineage["detail"] = "Manually added via dashboard"
    elif source:
        lineage["source_type"] = "llm"
        lineage["detail"] = f"LLM-extracted from session {source}"
        lineage["session_id"] = source
    return lineage


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("related_ids", "tags", "detail_ids"):
        raw = d.get(field)
        if isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    rr = d.get("related_reasoning")
    if isinstance(rr, str):
        try:
            d["related_reasoning"] = json.loads(rr)
        except (json.JSONDecodeError, TypeError):
            d["related_reasoning"] = {}
    d["grade"] = evidence_based_grade(d)
    d["lineage"] = _build_lineage(d)
    return d


def _fetch_one(kid: str) -> dict:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM knowledge WHERE id = ?", (kid,)).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Knowledge {kid} not found")
    return _row_to_dict(row)


# ─── API ─────────────────────────────────────────────────────

@app.get("/api/knowledge")
def list_knowledge(include_archived: bool = False):
    db = get_db()
    try:
        query = "SELECT * FROM knowledge"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY domain, confidence DESC"
        rows = db.execute(query).fetchall()
    finally:
        db.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/knowledge/{kid}")
def get_one(kid: str):
    return _fetch_one(kid)


@app.post("/api/knowledge")
def create_knowledge(item: KnowledgeIn):
    kid = item.id or f"manual-{int(datetime.now().timestamp() * 1000)}"
    payload = {
        "id": kid,
        "type": item.type,
        "domain": item.domain,
        "title": item.title,
        "content": item.content,
        "reasoning": item.reasoning or "",
        "confidence": item.confidence,
        "source_session_id": item.source_session_id,
        "related_ids": item.related_ids,
        "tags": item.tags,
        "usage_count": 0,
        "approved": item.approved,
        "status": item.status or "standalone",
        "parent_id": None,
        "detail_ids": [],
    }
    db = get_db()
    try:
        save_knowledge(db, payload)
    finally:
        db.close()
    return _fetch_one(kid)


@app.put("/api/knowledge/{kid}")
def update_knowledge(kid: str, patch: KnowledgeUpdate):
    current = _fetch_one(kid)
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        return current

    set_parts = []
    values = []
    for k, v in fields.items():
        if k in ("related_ids", "tags"):
            v = json.dumps(v, ensure_ascii=False)
        set_parts.append(f"{k} = ?")
        values.append(v)
    set_parts.append("updated_at = ?")
    values.append(datetime.now().isoformat())
    values.append(kid)

    db = get_db()
    try:
        db.execute(
            f"UPDATE knowledge SET {', '.join(set_parts)} WHERE id = ?",
            values,
        )
        db.commit()
    finally:
        db.close()
    return _fetch_one(kid)


@app.delete("/api/knowledge/{kid}")
def delete_knowledge(kid: str):
    _fetch_one(kid)  # 404 if missing
    db = get_db()
    try:
        db.execute("DELETE FROM knowledge WHERE id = ?", (kid,))
        db.commit()
    finally:
        db.close()
    return {"deleted": kid}


@app.post("/api/knowledge/{kid}/approve")
def approve_knowledge(kid: str):
    db = get_db()
    try:
        db.execute(
            "UPDATE knowledge SET approved = 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), kid),
        )
        db.commit()
    finally:
        db.close()
    return _fetch_one(kid)


@app.post("/api/knowledge/{kid}/add-related/{related_id}")
async def add_related(kid: str, related_id: str, request: Request):
    """두 항목 간 새 관계를 양방향으로 추가."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reasoning = (body or {}).get("reasoning", "")

    db = get_db()
    try:
        for item_id, target_id in [(kid, related_id), (related_id, kid)]:
            row = db.execute(
                "SELECT related_ids, related_reasoning FROM knowledge WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                continue
            r = dict(row)
            try:
                ids = json.loads(r["related_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                ids = []
            if target_id not in ids:
                ids.append(target_id)
            try:
                reasons = json.loads(r["related_reasoning"] or "{}")
            except (json.JSONDecodeError, TypeError):
                reasons = {}
            if reasoning:
                reasons[target_id] = reasoning
            db.execute(
                "UPDATE knowledge SET related_ids = ?, related_reasoning = ?, updated_at = ? WHERE id = ?",
                (json.dumps(ids), json.dumps(reasons, ensure_ascii=False), datetime.now().isoformat(), item_id),
            )
        db.commit()
    finally:
        db.close()
    return {"added": True, "source": kid, "target": related_id}


@app.post("/api/knowledge/{kid}/remove-related/{related_id}")
def remove_related(kid: str, related_id: str):
    """두 항목 간 관계를 양방향으로 제거."""
    db = get_db()
    try:
        for item_id, target_id in [(kid, related_id), (related_id, kid)]:
            row = db.execute(
                "SELECT related_ids, related_reasoning FROM knowledge WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                continue
            r = dict(row)
            try:
                ids = json.loads(r["related_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                ids = []
            if target_id in ids:
                ids.remove(target_id)
            try:
                reasons = json.loads(r["related_reasoning"] or "{}")
            except (json.JSONDecodeError, TypeError):
                reasons = {}
            reasons.pop(target_id, None)
            db.execute(
                "UPDATE knowledge SET related_ids = ?, related_reasoning = ?, updated_at = ? WHERE id = ?",
                (json.dumps(ids), json.dumps(reasons, ensure_ascii=False), datetime.now().isoformat(), item_id),
            )
        db.commit()
    finally:
        db.close()
    return {"removed": True, "source": kid, "target": related_id}


@app.put("/api/knowledge/{kid}/related-reasoning/{related_id}")
async def update_related_reasoning(kid: str, related_id: str, request: Request):
    """특정 관계의 reasoning을 수정."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_reason = (body or {}).get("reasoning", "")

    db = get_db()
    try:
        row = db.execute(
            "SELECT related_reasoning FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        if not row:
            db.close()
            raise HTTPException(status_code=404, detail="not found")
        try:
            reasons = json.loads(dict(row)["related_reasoning"] or "{}")
        except (json.JSONDecodeError, TypeError):
            reasons = {}
        reasons[related_id] = new_reason
        db.execute(
            "UPDATE knowledge SET related_reasoning = ?, updated_at = ? WHERE id = ?",
            (json.dumps(reasons, ensure_ascii=False), datetime.now().isoformat(), kid),
        )
        db.commit()
    finally:
        db.close()
    return {"updated": True, "source": kid, "target": related_id, "reasoning": new_reason}


@app.post("/api/knowledge/{kid}/archive")
def archive_knowledge(kid: str):
    db = get_db()
    try:
        db.execute(
            "UPDATE knowledge SET archived = 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), kid),
        )
        db.commit()
    finally:
        db.close()
    return _fetch_one(kid)


def _structure_details(detail_ids: list[str]) -> dict:
    """Collapse a list of detail rows into a new summary via Sonnet.

    Returns the saved summary dict plus {cost_usd, details_linked}.
    """
    if not detail_ids:
        raise HTTPException(status_code=400, detail="detail_ids required")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="ANTHROPIC_API_KEY not set. Enable LLM structuring by exporting the key, or refine manually.",
        )

    db = get_db()
    try:
        placeholders = ",".join("?" * len(detail_ids))
        rows = db.execute(
            f"SELECT * FROM knowledge WHERE id IN ({placeholders}) AND status = 'detail'",
            detail_ids,
        ).fetchall()
        details = [_row_to_dict(r) for r in rows]
        if not details:
            raise HTTPException(status_code=404, detail="No matching detail items")

        existing_rows = db.execute(
            "SELECT id, title, content, domain FROM knowledge "
            "WHERE status IN ('summary','standalone') AND archived = 0 "
            "LIMIT 30"
        ).fetchall()
        existing_summary = "\n".join(
            f"- [{r['id']}] ({r['domain']}) {r['title']}: {(r['content'] or '')[:80]}"
            for r in existing_rows
        ) or "(none)"

        detail_text = "\n\n".join(
            f"[{d['id']}] ({d['type']}) {d['title']}\n{d['content']}"
            + (f"\nReasoning: {d['reasoning']}" if d.get("reasoning") else "")
            for d in details
        )

        prompt = (
            "You are structuring raw knowledge into a clean, actionable summary.\n\n"
            "Raw detail items to synthesize:\n"
            f"{detail_text}\n\n"
            "Existing knowledge (for relationship discovery — find connections, avoid duplication):\n"
            f"{existing_summary}\n\n"
            "Create ONE structured summary that captures the essential knowledge from "
            "ALL detail items above.\n\n"
            "Rules:\n"
            "1. title: Clear imperative, under 50 chars\n"
            "2. content: Actionable rule/pattern, under 150 words. Self-contained.\n"
            "3. reasoning: Why this matters, under 80 words\n"
            "4. tags: 3-7 domain-specific tags\n"
            "5. domain: Infer from detail items\n"
            "6. type: domain_rule | architecture_decision | debug_playbook | "
            "anti_pattern | tool_preference | code_pattern\n"
            "7. related_ids: From existing knowledge list, items sharing domain context\n"
            "8. related_reasoning: {id: one-sentence reason} per related item\n"
            "9. supersedes: id of a standalone item this replaces, or null\n\n"
            "Return ONLY JSON with keys: title, content, reasoning, type, domain, "
            "tags, confidence (0-1), related_ids, related_reasoning, supersedes."
        )

        try:
            import anthropic
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"anthropic SDK not installed: {e}")

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {e}")

        summary_id = f"sum-{hashlib.md5(f'{time.time()}-{detail_ids[0]}'.encode()).hexdigest()[:8]}"
        confidence = float(result.get("confidence", 0.85))
        summary = {
            "id": summary_id,
            "type": result.get("type", details[0]["type"]),
            "domain": result.get("domain", details[0]["domain"]),
            "title": result["title"],
            "content": result["content"],
            "reasoning": result.get("reasoning", ""),
            "confidence": confidence,
            "source_session_id": f"structured-from-{len(details)}-details",
            "related_ids": result.get("related_ids", []),
            "related_reasoning": result.get("related_reasoning", {}),
            "tags": result.get("tags", []),
            "approved": 1 if confidence >= 0.9 else 0,
            "status": "summary",
            "detail_ids": detail_ids,
            "parent_id": None,
        }
        save_knowledge(db, summary)

        for did in detail_ids:
            link_detail_to_summary(db, did, summary_id)

        usage = response.usage
        cost = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
        log_extraction_cost(
            db, summary_id, "structure", "sonnet",
            usage.input_tokens, usage.output_tokens, cost,
        )

        superseded = result.get("supersedes")
        if superseded:
            db.execute(
                "UPDATE knowledge SET archived = 1, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), superseded),
            )
            db.commit()

        saved = _fetch_one(summary_id)
        saved["cost_usd"] = round(cost, 6)
        saved["details_linked"] = len(detail_ids)
        saved["superseded"] = superseded
        return saved
    finally:
        db.close()


@app.post("/api/knowledge/structure")
def structure_details(req: StructureRequest):
    return _structure_details(req.detail_ids)


@app.post("/api/knowledge/{kid}/structure")
def structure_single(kid: str):
    return _structure_details([kid])


@app.post("/api/knowledge/fill-reasoning")
def fill_reasoning():
    """Fill missing related_reasoning for existing related_ids pairs with Haiku.

    Finds every non-archived item that has related_ids but empty/no
    related_reasoning, then asks Haiku for one-sentence justifications.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="ANTHROPIC_API_KEY not set. Export it before calling this endpoint.",
        )

    try:
        import anthropic
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"anthropic SDK not installed: {e}")

    client = anthropic.Anthropic()
    db = get_db()
    try:
        rows = db.execute("""
            SELECT id, title, content, domain, related_ids, related_reasoning
              FROM knowledge
             WHERE archived = 0
               AND related_ids IS NOT NULL
               AND related_ids != '[]'
               AND related_ids != ''
        """).fetchall()

        filled = 0
        total_cost = 0.0
        errors: list[str] = []

        for row in rows:
            item = dict(row)
            try:
                rel_ids = json.loads(item["related_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                rel_ids = []
            try:
                existing = json.loads(item["related_reasoning"] or "{}")
            except (json.JSONDecodeError, TypeError):
                existing = {}
            missing = [rid for rid in rel_ids if rid and rid not in existing]
            if not missing:
                continue

            placeholders = ",".join("?" * len(missing))
            rel_rows = db.execute(
                f"SELECT id, title, content FROM knowledge WHERE id IN ({placeholders})",
                missing,
            ).fetchall()
            if not rel_rows:
                continue

            pairs_text = "\n".join(
                f"- {r['id']}: {r['title']} — {(r['content'] or '')[:80]}"
                for r in [dict(x) for x in rel_rows]
            )
            prompt = (
                f'Given this knowledge item:\n"{item["title"]}": '
                f'{(item["content"] or "")[:120]}\n\n'
                f"And these related items:\n{pairs_text}\n\n"
                'For each related item, write ONE short sentence (under 15 words) '
                'explaining WHY they are related. '
                'Return ONLY JSON {"item-id": "reason", ...}.'
            )

            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                reasons = json.loads(text)
                if not isinstance(reasons, dict):
                    raise ValueError("not a dict")
            except Exception as e:
                errors.append(f"{item['id']}: {e}")
                continue

            merged = {**existing, **reasons}
            db.execute(
                "UPDATE knowledge SET related_reasoning = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False),
                 datetime.now().isoformat(),
                 item["id"]),
            )

            usage = response.usage
            cost = (usage.input_tokens * 0.25 + usage.output_tokens * 1.25) / 1_000_000
            total_cost += cost
            log_extraction_cost(
                db, item["id"], "filter", "haiku",
                usage.input_tokens, usage.output_tokens, cost,
            )
            filled += 1

        db.commit()
        return {
            "filled": filled,
            "total_cost": round(total_cost, 6),
            "errors": errors[:5],
        }
    finally:
        db.close()


@app.get("/api/knowledge/{kid}/details")
def list_details(kid: str):
    _fetch_one(kid)
    db = get_db()
    try:
        return get_details_for(db, kid)
    finally:
        db.close()


@app.get("/api/grades")
def grades():
    """Grade metadata (letter → label/color/min)."""
    return CONFIDENCE_GRADES


@app.get("/api/stats")
def stats():
    db = get_db()
    try:
        return get_stats(db)
    finally:
        db.close()


@app.get("/api/domains")
def domains():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT domain, COUNT(*) AS cnt FROM knowledge "
            "WHERE archived = 0 GROUP BY domain ORDER BY domain"
        ).fetchall()
    finally:
        db.close()

    tree: dict = {}
    for r in rows:
        full = r["domain"]
        cnt = r["cnt"]
        parts = full.split("/")
        top = "/".join(parts[:2]) if len(parts) >= 2 else full
        node = tree.setdefault(top, {"count": 0, "subs": {}})
        node["count"] += cnt
        if full != top:
            node["subs"][full] = cnt
    return tree


# ─── Static SPA ──────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def main():
    import argparse
    import uvicorn
    import webbrowser

    parser = argparse.ArgumentParser(description="Mojo Dashboard")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--no-browser", action="store_true", help="Skip auto-opening browser")
    args = parser.parse_args()

    init_db()
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"Mojo Dashboard running at {url}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
