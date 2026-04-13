"""Export the Mojo dashboard as a single self-contained, read-only HTML file.

The resulting file bundles every GET response the SPA needs into a
``window.__MOJO_SNAPSHOT__`` blob and monkey-patches ``window.fetch`` so
``/api/*`` requests are served from that snapshot without a backend.
Mutating requests (POST / PUT / DELETE) return a 403 so the UI fails
gracefully — this build is strictly for viewing.

Usage:
    mojo dashboard-export                       # → mojo-dashboard.html
    mojo dashboard-export -o share/mojo.html    # custom path
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import evidence_based_grade, get_db  # noqa: E402

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


def _build_lineage(item: dict) -> dict:
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


def _collect_snapshot() -> dict:
    db = get_db()
    try:
        active_rows = db.execute(
            "SELECT * FROM knowledge WHERE archived = 0 "
            "ORDER BY domain, confidence DESC"
        ).fetchall()
        all_rows = db.execute(
            "SELECT * FROM knowledge ORDER BY domain, confidence DESC"
        ).fetchall()

        active = [_row_to_dict(r) for r in active_rows]
        everything = [_row_to_dict(r) for r in all_rows]

        details: dict[str, list[dict]] = {}
        for item in everything:
            detail_rows = db.execute(
                "SELECT * FROM knowledge WHERE parent_id = ? AND archived = 0 "
                "ORDER BY created_at",
                (item["id"],),
            ).fetchall()
            details[item["id"]] = [_row_to_dict(r) for r in detail_rows]
    finally:
        db.close()

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "knowledge_active": active,
        "knowledge_all": everything,
        "details": details,
    }


_SHIM_TEMPLATE = """
<script>
// ── Mojo static snapshot ─────────────────────────────────────
window.__MOJO_SNAPSHOT__ = __SNAPSHOT_JSON__;
(function () {
  const snap = window.__MOJO_SNAPSHOT__;
  const originalFetch = window.fetch ? window.fetch.bind(window) : null;

  function json(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { "content-type": "application/json" },
    });
  }
  function forbidden(msg) {
    return json({ detail: msg || "read-only snapshot" }, 403);
  }

  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();

    if (!url.startsWith("/api/")) {
      return originalFetch ? originalFetch(input, init) : forbidden("offline");
    }

    // Mutating calls are disabled in the static export.
    if (method !== "GET") return Promise.resolve(forbidden("This is a read-only snapshot."));

    // GET /api/knowledge?include_archived=...
    if (/^\\/api\\/knowledge(?:\\?|$)/.test(url)) {
      const includeArchived = /include_archived=true/.test(url);
      return Promise.resolve(json(includeArchived ? snap.knowledge_all : snap.knowledge_active));
    }
    // GET /api/knowledge/{id}/details
    let m = url.match(/^\\/api\\/knowledge\\/([^/]+)\\/details$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      return Promise.resolve(json(snap.details[id] || []));
    }
    // GET /api/knowledge/{id}
    m = url.match(/^\\/api\\/knowledge\\/([^/]+)$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const item = snap.knowledge_all.find((k) => k.id === id);
      return Promise.resolve(item ? json(item) : json({ detail: "not found" }, 404));
    }
    return Promise.resolve(json({ detail: "not available in snapshot" }, 404));
  };
})();
</script>
<style>
  #mojo-snapshot-banner {
    position: fixed; left: 50%; top: 12px; transform: translateX(-50%);
    z-index: 9999; padding: 6px 14px; border-radius: 999px;
    background: rgba(78, 205, 196, 0.14);
    border: 1px solid rgba(78, 205, 196, 0.45);
    color: #4ECDC4; font-size: 12px; font-family: 'JetBrains Mono', monospace;
    pointer-events: none; letter-spacing: 0.02em;
  }
</style>
<div id="mojo-snapshot-banner">read-only snapshot · __GENERATED_AT__</div>
"""


def build_html(output: Path) -> Path:
    if not STATIC_INDEX.exists():
        raise FileNotFoundError(f"Dashboard template not found: {STATIC_INDEX}")

    snapshot = _collect_snapshot()
    shim = (
        _SHIM_TEMPLATE
        .replace("__SNAPSHOT_JSON__", json.dumps(snapshot, ensure_ascii=False))
        .replace("__GENERATED_AT__", snapshot["generated_at"])
    )

    html = STATIC_INDEX.read_text(encoding="utf-8")
    marker = '<div id="root"></div>'
    if marker not in html:
        raise RuntimeError("Could not locate <div id='root'> in index.html")
    html = html.replace(marker, marker + "\n" + shim, 1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Mojo dashboard as static HTML")
    parser.add_argument(
        "-o", "--output",
        default="mojo-dashboard.html",
        help="Output HTML path (default: ./mojo-dashboard.html)",
    )
    args = parser.parse_args()

    out = build_html(Path(args.output).expanduser().resolve())
    size_kb = out.stat().st_size / 1024
    print(f"✓ Wrote {out} ({size_kb:.1f} KB)")
    print("  Open it in any browser — no server needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
