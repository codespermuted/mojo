# Classification facets: `intent` × `subject`

> Status: adopted. `type` (6 values) is retained for backward-compat but is no
> longer the 1st-class label. New extractions emit `intent` + `subject`; old
> rows were backfilled (`scripts/backfill_facets.py`).

## Why the old `type` axis was wrong

The legacy `knowledge.type` enum has six values:
`domain_rule · architecture_decision · debug_playbook · anti_pattern ·
tool_preference · code_pattern`.

An audit of the live store (80 active cards) showed it is **not MECE**:

1. **Redundant with `taxon`.** `code_pattern`→`implementation_pattern` 92%,
   `debug_playbook`→`debugging_pattern` 70%, `tool_preference`→`tool_usage_rule`
   — `type` was a coarse copy of `taxon`, not an orthogonal axis.
2. **One concept fragments across types (ME broken).** The 11 ML
   `leakage_warning` cards landed in `domain_rule` (6), `anti_pattern` (4) and
   `debug_playbook` (1). "Operational-data leakage" — the single most important
   category for this user — had no home and scattered across 3 types.
3. **ML categories missing (CE broken).** `leakage_warning`,
   `environment_constraint`, `deployment_note` have no slot in the 6 types and
   were force-fit elsewhere.

Root cause: the six values mix **three different division axes** —
motive (`domain_rule`/`architecture_decision`/`anti_pattern`),
use (`debug_playbook`), and target (`tool_preference`/`code_pattern`). Mixing
the *fundamentum divisionis* in one enum makes mutual exclusivity impossible.

## The scheme

Two orthogonal facets, each near-MECE within itself.

### `intent` — the knowledge's MOTIVE
| value | meaning |
|-------|---------|
| `constraint` | a fact/rule/prohibition/pitfall to honor (`"always X"` or `"don't Y — it breaks/leaks"`). **Merges plain rules and warnings** — they are one prescriptive spectrum. |
| `decision` | a design choice picked among alternatives after weighing trade-offs. |
| `playbook` | a diagnostic/debugging procedure (symptom → check/fix). |
| `preference` | a tool/library taste preference. |
| `open_question` | an unresolved question. |

### `subject` — what the knowledge is ABOUT
| value | meaning |
|-------|---------|
| `data` | operational-data correctness: availability / point-in-time / leakage / timezone / grain / source integration. |
| `model` | modeling: architecture, loss, training, evaluation, regime, feature engineering, calibration. |
| `pipeline` | code/pipeline mechanism: caching, feature-set branching, status sentinels, UI render, parsing. |
| `tooling` | meta-tool architecture: mojo / mlmon / the harness / session monitoring. |
| `external` | third-party API / library / CLI quirks (data.go.kr, sqlite, uv, Lightning config behavior). |

## Audit result (80 cards relabeled)

```
intent \ subject     data   model  pipeline  tooling  external   sum
  constraint           13      22       3        3        3        44
  decision              1      14       2       10        0        27
  playbook              0       6       1        1        1         9
```

- **Fragmentation resolved.** The 14 operational-data-correctness cards were
  spread across **5 of 6 legacy types**; under the new scheme they collapse to
  **`subject = data`** (one column).
- **`warning`/`rule` were over-split.** An initial 6-value intent draft put
  ambiguity at 52% because `warning` and `rule` are the same prescriptive
  spectrum; merging them into `constraint` dropped intent ambiguity to 21%
  (the remainder is the genuine `constraint↔decision` grey zone).

## Inter-rater reliability (Cohen's κ)

Two independent raters (the author + a blind LLM rater given only the rubric,
not the author's labels) classified all 80 cards:

| axis | agreement | Cohen's κ |
|------|-----------|-----------|
| `subject` | 80/80 (100%) | **1.000** |
| `intent`  | 74/80 (92.5%) | **0.870** (almost-perfect, Landis–Koch) |

All 6 intent disagreements are `constraint↔decision` / `constraint↔playbook`
boundary cards (`cc-001`, `diff-003`, `hook-001`, `lit-001`, `now-001`,
`smp-004-b8`); **zero** subject disagreements. The grey zone is intrinsic
(e.g. "use month-grain fuel cost" reads as both a constraint and a design
choice) and is resolved by a constraint-wins tie-break rule.

## Mechanics

- Schema: `intent`, `subject` columns on `knowledge` (nullable; see
  `db/schema.sql`). Added in-place by `db_ops.init_db()` migrations.
- New extractions: `extract/prompts/structure.xml` emits both fields.
- Fallback: `db_ops.default_facets(type, taxon)` derives a coarse
  `(intent, subject)` when an item omits them.
- Backfill of pre-existing rows: `python scripts/backfill_facets.py`
  (idempotent; labels in `seeds/facets_backfill.json`).
