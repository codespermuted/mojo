"""Mojo web dashboard — FastAPI backend + static SPA.

Run: python dashboard/server.py
Opens http://localhost:8765 in the default browser.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (  # noqa: E402
    CONFIDENCE_GRADES,
    evidence_based_grade,
    get_db,
    get_stats,
    init_db,
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
    for field in ("related_ids", "tags"):
        raw = d.get(field)
        if isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[field] = []
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
    return FileResponse(str(STATIC_DIR / "index.html"))


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
