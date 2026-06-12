#!/usr/bin/env python3
"""One-time backfill: assign (intent, subject) facets to existing knowledge.

The per-card labels in ``seeds/facets_backfill.json`` come from a manual audit
of the live store against a fixed rubric (see ``docs/FACETS.md``). The audit
showed the legacy single ``type`` axis mixed motive/use/target, so one concept
(e.g. an operational-data leakage warning) fragmented across 5 of 6 types.
Splitting motive (``intent``) from target (``subject``) makes each axis
near-MECE; ``warning`` and ``rule`` were merged into ``constraint`` because they
are one prescriptive spectrum.

Cards absent from the seed fall back to ``db_ops.default_facets`` derived from
their legacy type/taxon. Idempotent — re-running overwrites the same rows.

Usage:  python scripts/backfill_facets.py [--db PATH]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db_ops  # noqa: E402

SEED = ROOT / "seeds" / "facets_backfill.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=None,
                    help="DB path (default: ~/.mojo/mojo.db)")
    args = ap.parse_args()

    db_ops.init_db(args.db)          # ensure intent/subject columns exist
    db = db_ops.get_db(args.db)

    labels = json.loads(SEED.read_text())
    seeded = 0
    for cid, f in labels.items():
        cur = db.execute(
            "UPDATE knowledge SET intent = ?, subject = ? WHERE id = ?",
            (f["intent"], f["subject"], cid),
        )
        seeded += cur.rowcount

    # Fallback for any card still missing a facet (not in the seed).
    missing = db.execute(
        "SELECT id, type, taxon FROM knowledge "
        "WHERE intent IS NULL OR subject IS NULL"
    ).fetchall()
    for r in missing:
        i, s = db_ops.default_facets(r["type"], r["taxon"])
        db.execute(
            "UPDATE knowledge SET intent = COALESCE(intent, ?), "
            "subject = COALESCE(subject, ?) WHERE id = ?",
            (i, s, r["id"]),
        )
    db.commit()

    total = db.execute("SELECT COUNT(*) c FROM knowledge WHERE archived = 0").fetchone()["c"]
    still_null = db.execute(
        "SELECT COUNT(*) c FROM knowledge WHERE archived = 0 "
        "AND (intent IS NULL OR subject IS NULL)"
    ).fetchone()["c"]
    print(f"seeded {seeded} rows from {SEED.name}, fallback-filled {len(missing)}")
    print(f"active cards: {total}, still unlabeled: {still_null}")

    grid = Counter()
    for r in db.execute(
        "SELECT intent, subject FROM knowledge WHERE archived = 0"
    ):
        grid[(r["intent"], r["subject"])] += 1
    subs = list(db_ops.VALID_SUBJECTS)
    print("\nintent \\ subject  " + "".join(f"{s:>9}" for s in subs) + "    sum")
    for i in db_ops.VALID_INTENTS:
        row = [grid.get((i, s), 0) for s in subs]
        if sum(row):
            print(f"  {i:<14}" + "".join(f"{x:>9}" for x in row) + f"  {sum(row):>5}")


if __name__ == "__main__":
    main()
