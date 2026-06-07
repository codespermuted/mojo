"""Obsidian vault export/import: knowledge as human-readable markdown.

Ownership split (what makes two-way sync safe without timestamps):

- The **vault file wins** for human-editable fields: title, content,
  reasoning, scope, taxon, domain, applies_when, does_not_apply_when,
  evidence_level, promotion_state, approved, archived, safe_to_generalize,
  confidence, tags. Reviewing knowledge = editing frontmatter in Obsidian.
- The **DB wins** for machine fields: observations, related links, usage,
  grades, lineage, generalization_suggested. These are regenerated on
  every export.

``sync`` therefore always runs import (apply human edits) before export
(rewrite normalized files). One file per claim:

    <vault>/knowledge/<domain>/<id> <title-slug>.md

plus an auto-generated ``REVIEW-QUEUE.md`` at the vault root listing items
awaiting review and generalization candidates — Obsidian replaces the old
web dashboard as the review UI.
"""

from __future__ import annotations

import json
import glob
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))

from db_ops import (
    evidence_based_grade,
    get_db,
    get_observations,
    init_db,
    knowledge_tier,
    set_promotion_state,
    VALID_EVIDENCE_LEVELS,
    VALID_PROMOTION_STATES,
    VALID_SCOPES,
    VALID_TAXA,
)
from mojo_config import load_config

console = Console()

KNOWLEDGE_DIR = "knowledge"
REVIEW_QUEUE_FILE = "REVIEW-QUEUE.md"

# Fields the vault file owns. Order = frontmatter order.
EDITABLE_FIELDS = [
    "title", "type", "taxon", "domain", "scope",
    "evidence_level", "promotion_state", "approved", "archived",
    "safe_to_generalize", "confidence", "applies_when",
    "does_not_apply_when", "tags",
]
BOOL_FIELDS = {"approved", "archived", "safe_to_generalize"}

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

def get_vault_path(config: dict | None = None) -> Path:
    config = config or load_config()
    raw = (config.get("vault") or {}).get("path") or "~/mojo-vault"
    return Path(raw).expanduser()


def _slugify(title: str, max_len: int = 60) -> str:
    """Filesystem/Obsidian-safe slice of a title (Korean kept as-is)."""
    slug = re.sub(r'[\\/:#^\[\]|?*"<>\n\t]', " ", title)
    slug = re.sub(r"\s+", " ", slug).strip()
    return slug[:max_len].strip() or "untitled"


def _domain_parts(domain: str) -> list[str]:
    """Domain → folder segments, kept simple and intuitive.

    Drop a leading segment that equals the vault's knowledge dir name, so a
    domain like "knowledge/management" maps to knowledge/management/ rather
    than the confusing knowledge/knowledge/management/.
    """
    parts = [p for p in (domain or "misc").split("/") if p]
    if parts and parts[0].lower() == KNOWLEDGE_DIR:
        parts = parts[1:]
    return parts or ["misc"]


def _item_file(vault: Path, item: dict) -> Path:
    domain_dir = vault / KNOWLEDGE_DIR
    for part in _domain_parts(item.get("domain") or "misc"):
        domain_dir = domain_dir / _slugify(part, 40)
    return domain_dir / f"{item['id']} {_slugify(item.get('title', ''))}.md"


def _find_existing(vault: Path, item_id: str) -> Path | None:
    """Locate an item's file anywhere under knowledge/ (domain may change).

    ``glob.escape`` keeps ids with glob metacharacters literal, and the
    results are sorted so a (corruption-induced) duplicate resolves
    deterministically instead of whichever the filesystem yields first.
    """
    root = vault / KNOWLEDGE_DIR
    if not root.exists():
        return None
    matches = sorted(root.rglob(f"{glob.escape(item_id)} *.md"))
    return matches[0] if matches else None


# ─────────────────────────────────────────────────────────────────────────────
# Render (DB → md)
# ─────────────────────────────────────────────────────────────────────────────

def _clean_excerpt(excerpt: str, limit: int = 1500) -> str:
    """Drop tool-instrumentation noise from a transcript excerpt.

    Old extractions captured lines like ``[CLAUDE]:`` with an empty body
    and ``[Tools used: Edit]`` that carry no evidence. Keep only lines with
    real prose so the excerpt reads as evidence, not a tool log.
    """
    kept = []
    for ln in excerpt.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s in ("[CLAUDE]:", "[USER]:"):
            continue
        if s.startswith("[Tools used:") and s.endswith("]"):
            continue
        kept.append(ln)
    return "\n".join(kept)[:limit]


def render_item(item: dict, observations: list[dict],
                filename_by_id: dict[str, str]) -> str:
    # Frontmatter holds ONLY human-editable fields — editing them IS the
    # review. Machine fields go in a footer so the note opens on the claim,
    # not a wall of metadata (theme: simple, intuitive).
    meta: dict = {"id": item["id"]}
    for field in EDITABLE_FIELDS:
        value = item.get(field)
        if field in BOOL_FIELDS:
            value = bool(value)
        if field == "confidence" and value is not None:
            value = round(float(value), 2)
        if field == "tags":
            value = list(value or [])
        meta[field] = value if value is not None else ""

    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                        default_flow_style=False, width=88).strip()

    lines = ["---", fm, "---", ""]
    lines += [f"# {item.get('title', item['id'])}", ""]
    lines += [(item.get("content") or "").strip(), ""]

    if item.get("reasoning"):
        lines += ["## Why", "", item["reasoning"].strip(), ""]

    if item.get("applies_when") or item.get("does_not_apply_when"):
        lines += ["## Scope boundary", ""]
        if item.get("applies_when"):
            lines += [f"- **적용**: {item['applies_when'].strip()}"]
        if item.get("does_not_apply_when"):
            lines += [f"- **부적용**: {item['does_not_apply_when'].strip()}"]
        lines += [""]

    if observations:
        lines += ["## Observations", "",
                  "| date | project | stance | note |",
                  "|---|---|---|---|"]
        for ob in observations:
            date = (ob.get("observed_at") or "")[:10]
            proj = ob.get("project_path") or ""
            proj_name = Path(proj).name if proj else "—"
            note = (ob.get("note") or "").replace("|", "\\|").replace("\n", " ")
            stance = "✅ supports" if ob.get("stance") == "supports" else "❌ refutes"
            lines += [f"| {date} | {proj_name} | {stance} | {note} |"]
        lines += [""]

    related = item.get("related_ids") or []
    if related:
        reasoning_map = item.get("related_reasoning") or {}
        lines += ["## Related", ""]
        for rid in related:
            target_name = filename_by_id.get(rid)
            link = f"[[{target_name}]]" if target_name else f"`{rid}`"
            why = reasoning_map.get(rid, "")
            lines += [f"- {link}" + (f" — {why}" if why else "")]
        lines += [""]

    excerpt = _clean_excerpt((item.get("evidence_excerpt") or "").strip())
    if excerpt:
        quoted = "\n".join(f"> {ln}" for ln in excerpt.splitlines())
        lines += ["## Evidence excerpt", "", quoted, ""]

    # Machine-owned footer — regenerated every export; edits here are lost.
    lineage = item.get("source_lineage") or {}
    proj = item.get("project_path") or "—"
    lines += [
        "## Metadata (auto — do not edit)", "",
        f"- grade: **{evidence_based_grade(item)}** · tier: {knowledge_tier(item)}"
        f" · generalization_suggested: {bool(item.get('generalization_suggested'))}",
        f"- project: `{proj}` · usage: {item.get('usage_count', 0)}",
        f"- created: {item.get('created_at') or '—'} · updated: {item.get('updated_at') or '—'}",
    ]
    if lineage:
        lines += [f"- source: `{json.dumps(lineage, ensure_ascii=False)}`"]
    lines += [""]

    return "\n".join(lines).rstrip() + "\n"


def _queue_link(it: dict, filename_by_id: dict[str, str]) -> str:
    """An Obsidian link whose target is the EXACT filename (so it resolves),
    with a short alias for readability: [[full filename|id short-title]]."""
    target = filename_by_id.get(it["id"], f"{it['id']} {it.get('title', '')}")
    title = (it.get("title") or "").strip()
    short = title if len(title) <= 45 else title[:44].rstrip() + "…"
    alias = f"{it['id']} {short}".strip()
    return f"[[{target}|{alias}]]"


def render_review_queue(items: list[dict],
                        filename_by_id: dict[str, str] | None = None) -> str:
    """REVIEW-QUEUE.md — the human review inbox, replaces the dashboard."""
    filename_by_id = filename_by_id or {
        it["id"]: f"{it['id']} {_slugify(it.get('title', ''))}" for it in items
    }
    pending = [it for it in items
               if it.get("review_required") and not it.get("archived")
               and it.get("promotion_state") in ("raw", "candidate")]
    generalize = [it for it in items
                  if it.get("generalization_suggested") and not it.get("archived")]

    lines = [
        "# Review Queue",
        "",
        "> 자동 생성 파일 — 직접 편집하지 말 것. 각 노트의 frontmatter를 고쳐서",
        "> 리뷰한다: `promotion_state`(raw/candidate/project_approved/generalized/",
        "> rejected/archived), `scope`, `applies_when`, `does_not_apply_when`.",
        "> 저장 후 `mojo vault sync`가 DB에 반영한다.",
        "",
        f"## 일반화 후보 ({len(generalize)})",
        "",
        "서로 다른 프로젝트 ≥2곳에서 독립적으로 관찰된 주장. `scope`를 넓히고",
        "`promotion_state: generalized`로 바꿀지 검토하라.",
        "",
    ]
    for it in generalize:
        lines.append(f"- {_queue_link(it, filename_by_id)} — scope: {it.get('scope')}")
    if not generalize:
        lines.append("(없음)")
    lines += ["", f"## 검토 대기 ({len(pending)})", ""]
    # Highest confidence first within each domain — review the strongest
    # signals first (theme: intuitive, scannable).
    by_domain: dict[str, list] = defaultdict(list)
    for it in pending:
        by_domain[it.get("domain") or "misc"].append(it)
    for domain in sorted(by_domain):
        lines.append(f"### {domain}")
        rows = sorted(by_domain[domain],
                      key=lambda it: it.get("confidence") or 0.0, reverse=True)
        for it in rows:
            grade = evidence_based_grade(it)
            conf = it.get("confidence")
            conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
            lines.append(
                f"- `{grade} {conf_str}` {_queue_link(it, filename_by_id)}"
            )
        lines.append("")
    if not pending:
        lines.append("(없음)")
    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Export (DB → vault)
# ─────────────────────────────────────────────────────────────────────────────

def export_vault(quiet: bool = False) -> int:
    """Write every knowledge item to the vault. Returns file count."""
    config = load_config()
    vault = get_vault_path(config)
    init_db()
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM knowledge").fetchall()
        from db_ops import _row_to_dict
        items = [_row_to_dict(r) for r in rows]

        filename_by_id = {
            it["id"]: f"{it['id']} {_slugify(it.get('title', ''))}"
            for it in items
        }

        written = 0
        for item in items:
            observations = get_observations(db, item["id"])
            content = render_item(item, observations, filename_by_id)
            target = _item_file(vault, item)
            existing = _find_existing(vault, item["id"])
            if existing and existing != target:
                existing.unlink()  # renamed title or moved domain
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                target.write_text(content, encoding="utf-8")
                written += 1

        # Prune orphans: notes whose id no longer exists in the DB (item
        # deleted/archived-out). Guard: never prune when the DB returned no
        # items — a failed read must not wipe the vault.
        pruned = 0
        if items:
            pruned = _prune_orphans(vault, {it["id"] for it in items})

        vault.mkdir(parents=True, exist_ok=True)
        (vault / REVIEW_QUEUE_FILE).write_text(
            render_review_queue(items, filename_by_id), encoding="utf-8")
    finally:
        db.close()

    if not quiet:
        extra = f", {pruned} pruned" if pruned else ""
        console.print(f"[green]✓ Vault export: {written} file(s) updated{extra} "
                      f"→ {vault}[/green]")
    return written


_NOTE_ID_RE = re.compile(r"^(\S+) .+\.md$")


def _prune_orphans(vault: Path, live_ids: set[str]) -> int:
    """Delete knowledge notes whose id is not in ``live_ids``. Returns count."""
    root = vault / KNOWLEDGE_DIR
    if not root.exists():
        return 0
    pruned = 0
    for path in root.rglob("*.md"):
        m = _NOTE_ID_RE.match(path.name)
        if m and m.group(1) not in live_ids:
            path.unlink()
            pruned += 1
    # tidy up now-empty domain folders
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return pruned


# ─────────────────────────────────────────────────────────────────────────────
# Import (vault → DB) — apply human edits
# ─────────────────────────────────────────────────────────────────────────────

def _parse_note(text: str) -> tuple[dict, dict]:
    """Return (frontmatter, body_sections). Sections keyed by heading."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, {}
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, {}
    body = text[m.end():]

    sections: dict[str, str] = {}
    current = "_content"
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif line.startswith("# "):
            continue  # the H1 title mirrors frontmatter title
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return meta, sections


def _validate(field: str, value):
    """Reject frontmatter values that would corrupt enum fields."""
    validators = {
        "scope": VALID_SCOPES,
        "taxon": VALID_TAXA,
        "evidence_level": VALID_EVIDENCE_LEVELS,
        "promotion_state": VALID_PROMOTION_STATES,
    }
    if field in validators and value not in validators[field]:
        return False
    if field == "confidence":
        try:
            return 0.0 <= float(value) <= 1.0
        except (TypeError, ValueError):
            return False
    return True


def import_vault_edits(quiet: bool = False) -> int:
    """Apply human edits from vault files back to the DB.

    Returns the number of items changed. Only EDITABLE_FIELDS plus the
    content/reasoning body sections are read; machine sections are ignored.
    """
    config = load_config()
    vault = get_vault_path(config)
    root = vault / KNOWLEDGE_DIR
    if not root.exists():
        if not quiet:
            console.print(f"[dim]No vault at {root} — nothing to import.[/dim]")
        return 0

    init_db()
    db = get_db()
    changed_count = 0
    try:
        for path in sorted(root.rglob("*.md")):
            try:
                meta, sections = _parse_note(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            item_id = meta.get("id")
            if not item_id:
                continue
            row = db.execute(
                "SELECT * FROM knowledge WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                continue  # unknown id — leave the note alone
            from db_ops import _row_to_dict
            current = _row_to_dict(row)

            updates: dict = {}
            for field in EDITABLE_FIELDS:
                if field not in meta:
                    continue
                new_val = meta[field]
                if field in BOOL_FIELDS:
                    new_val = int(bool(new_val))
                    cur_val = int(bool(current.get(field)))
                elif field == "tags":
                    # Must be a YAML list. A bare string ("tags: foo") would
                    # become ['f','o','o'] under list() — reject it instead
                    # of silently shredding the field into characters.
                    if new_val in (None, ""):
                        new_val = []
                    elif not isinstance(new_val, list):
                        if not quiet:
                            console.print(
                                f"[yellow]{path.name}: tags must be a YAML list, "
                                f"got {type(new_val).__name__} — ignored[/yellow]")
                        continue
                    cur_val = list(current.get(field) or [])
                elif field == "confidence":
                    if not _validate(field, new_val):
                        continue
                    new_val = float(new_val)
                    cur_val = float(current.get(field) or 0.0)
                else:
                    new_val = (new_val or "").strip() if isinstance(new_val, str) else new_val
                    cur_val = (current.get(field) or "").strip()
                    if not _validate(field, new_val):
                        if not quiet:
                            console.print(
                                f"[yellow]{path.name}: invalid {field} "
                                f"'{new_val}' ignored[/yellow]")
                        continue
                if new_val != cur_val:
                    updates[field] = new_val

            # Body sections: content (text before first ##) and ## Why.
            # content must stay non-empty (a claim with no content is
            # meaningless — an empty body is almost always a parse artifact,
            # so we refuse to wipe it). reasoning is optional and MAY be
            # cleared, so an empty ## Why is recorded.
            if "_content" in sections:
                body_content = sections["_content"].strip()
                if body_content and body_content != (current.get("content") or "").strip():
                    updates["content"] = body_content
            if "Why" in sections:
                body_why = sections["Why"].strip()
                if body_why != (current.get("reasoning") or "").strip():
                    updates["reasoning"] = body_why

            if not updates:
                continue

            # promotion_state transitions carry side effects (approved,
            # archived, review_required) — route through the dedicated op.
            new_state = updates.pop("promotion_state", None)
            if new_state and new_state != current.get("promotion_state"):
                set_promotion_state(
                    db, item_id, new_state,
                    scope=updates.get("scope"),
                    evidence_level=updates.get("evidence_level"),
                )
            if updates:
                assignments = ", ".join(f"{f} = ?" for f in updates)
                params = [
                    json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
                    for v in updates.values()
                ]
                db.execute(
                    f"UPDATE knowledge SET {assignments}, "
                    f"updated_at = datetime('now') WHERE id = ?",
                    params + [item_id],
                )
                db.commit()
            changed_count += 1
            if not quiet:
                fields = list(updates) + (["promotion_state"] if new_state else [])
                console.print(f"[cyan]← {item_id}: {', '.join(fields)}[/cyan]")
    finally:
        db.close()

    if not quiet and changed_count:
        console.print(f"[green]✓ Vault import: {changed_count} item(s) updated[/green]")
    return changed_count


def sync_vault(quiet: bool = False) -> None:
    """Import human edits, then re-export normalized files."""
    import_vault_edits(quiet=quiet)
    export_vault(quiet=quiet)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

VAULT_README = """\
# Mojo Vault

Claude/Codex 세션에서 추출된 암묵지 저장소. Obsidian으로 연다.

- `knowledge/` — 주장(claim) 1개 = 노트 1개. frontmatter가 리뷰 UI다.
- `REVIEW-QUEUE.md` — 검토 대기·일반화 후보 인박스 (자동 생성).

## 리뷰 방법

노트의 frontmatter를 편집한 뒤 `mojo vault sync`:

- 승인: `promotion_state: project_approved` (이 프로젝트에서 참)
- 일반화: `promotion_state: generalized` + `scope: universal|domain`
- 기각: `promotion_state: rejected`, 보관: `archived`
- 경계 조정: `applies_when` / `does_not_apply_when`

frontmatter의 `grade`, `tier`, `observations` 등 기계 필드는 sync 때
재생성된다 — 편집해도 유지되지 않는다.
"""


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Obsidian vault sync (md ↔ knowledge DB)")
    parser.add_argument("action", nargs="?", default="sync",
                        choices=["sync", "export", "import", "init"],
                        help="sync = import edits then export (default)")
    args = parser.parse_args()

    if args.action == "init":
        vault = get_vault_path()
        (vault / KNOWLEDGE_DIR).mkdir(parents=True, exist_ok=True)
        readme = vault / "README.md"
        if not readme.exists():
            readme.write_text(VAULT_README, encoding="utf-8")
        console.print(f"[green]✓ Vault initialized at {vault}[/green]")
        export_vault()
    elif args.action == "export":
        export_vault()
    elif args.action == "import":
        import_vault_edits()
    else:
        sync_vault()


if __name__ == "__main__":
    main()
