# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-12

Reclassify knowledge on two orthogonal facets after an audit found the
single `type` axis was not MECE.

### Added
- **Classification facets `intent` × `subject`** (supersede the legacy single
  `type` axis, which is kept for backward-compat). `intent`: constraint |
  decision | playbook | preference | open_question. `subject`: data | model |
  pipeline | tooling | external. An audit of the live store showed `type` mixed
  three division axes, so one concept (operational-data leakage) fragmented
  across 5 of 6 types; the two facets are near-MECE (cross-validated Cohen's
  κ: subject 1.000, intent 0.870). See `docs/FACETS.md`.
- `db_ops.default_facets()` coarse fallback from legacy type/taxon, and
  `scripts/backfill_facets.py` (+ `seeds/facets_backfill.json`) to backfill
  existing rows. Extractor (`structure.xml`) now emits both facets.

### Changed
- Additive `intent`/`subject` columns on `knowledge` (in-place migration).
- Dashboard exposes facets via the API; default port `8765` → `8766`.

## [0.2.1] — 2026-06-08

Hardening pass after an adversarial multi-agent review of 0.2.0, plus
vault polish toward a simpler, more intuitive single-user experience.

### Fixed
- **ID collision / self-link**: an LLM-generated id colliding with an
  existing claim made `INSERT OR REPLACE` clobber the older claim and
  caused `find_related` to link an item to itself (observed live:
  `km-002 → km-002`). Collisions now get a fresh `-bN` id, and
  `find_related` excludes the item's own id.
- **JSON recovery**: prose-wrapped responses with two objects
  (`{…} and {…}`) produced invalid JSON via naïve `find`/`rfind`. Recovery
  now returns the first brace-balanced object (string-aware).
- **Self-capture**: the live `SessionEnd`/`Stop` hooks captured Mojo's own
  development sessions and bare `$HOME` sessions, polluting the store with
  meta-knowledge. `should_ignore_cwd` now skips both (mirrors the codex
  backfill repo-skip). Auto-extract log file descriptor is closed (no leak).
- **scan**: targeted Claude backfill now stores the real project path
  (consistent with codex + the hook); repo self-skip encoding handles `_`.
- **vault sync**: `tags:` as a bare string is rejected instead of being
  shredded into characters; clearing `## Why` now clears `reasoning`;
  `_find_existing` escapes glob metacharacters and sorts for determinism;
  `export` prunes orphan notes whose id left the DB (guarded against an
  empty-DB wipe).

### Changed
- **Simpler vault notes**: frontmatter holds only human-editable fields;
  machine fields (grade, tier, usage, timestamps, lineage) moved to a
  `## Metadata (auto)` footer so a note opens on the claim, not metadata.
- **REVIEW-QUEUE**: wikilinks target the exact filename with a short alias
  (were truncated/broken in Obsidian); pending items sort by confidence
  with a `grade conf` prefix.
- Domain `knowledge/…` no longer double-nests under `knowledge/knowledge/`.
- Evidence excerpts strip tool-log noise at render time.

## [0.2.0] — 2026-06-05

### Added
- **Pluggable LLM backends** (`extraction.backend` / `--backend`):
  `claude-cli` (headless `claude -p`, default, $0 on subscription),
  `codex-cli` (headless `codex exec`, $0), `api` (original Anthropic path,
  keeps `--batch` + cost tracking). The previously removed `claude-code`
  headless backend was dropped for unpredictable billing; `claude-cli`
  resolves that by stripping `ANTHROPIC_API_KEY` from the child env,
  forcing subscription auth — no key, no silent billing.
- **Codex session support**: rollout JSONL auto-detection in the parser
  (session_meta cwd → project, synthetic harness messages filtered) and
  `mojo scan sessions --source claude|codex|all` backfill.
- **Claim/observation model**: new `observations` table. Dedup no longer
  discards re-extractions — a re-observation is recorded as evidence;
  supporting observations from ≥ 2 distinct projects (no refutations) set
  `generalization_suggested`. Promotion remains a human decision.
- **Obsidian vault** (`mojo vault sync|export|import|init`): one claim =
  one markdown note under `vault.path` (default `~/mojo-vault`).
  Frontmatter is the review UI; ownership split keeps two-way sync safe
  (human fields ← vault, machine fields ← DB). Auto-generated
  `REVIEW-QUEUE.md` lists pending reviews and generalization candidates.
- **Auto-extraction on SessionEnd**: the hook spawns a detached
  `mojo extract --session ...` (log: `~/.mojo/logs/auto-extract.log`),
  gated by `extraction.auto_extract` and recursion-guarded via
  `MOJO_EXTRACTION=1` so headless extraction sessions never re-trigger
  themselves.
- `mojo_config.load_config()` — packaged defaults deep-merged with
  `~/.mojo/config.yaml`.

### Changed
- `--parallel` prefilter is now thread-based and works with every backend
  (was asyncio + Anthropic-only).
- `anthropic` is imported lazily — CLI-backend users don't need the package
  configured.

### Added (carried from pre-0.2.0 unreleased work)
- `mojo dashboard-export` — bundles the dashboard into a single read-only HTML file
  with an embedded snapshot, so knowledge views can be shared without a backend.
- `mojo extract --project <path>` / `-p` — scope extraction to one project. Defaults
  to the current working directory; use `--project all` to process every pending
  session.
- Auto-load `.env` from cwd / repo root so `ANTHROPIC_API_KEY` works without a
  manual `export`.
- GitHub Actions CI running `pytest` on Python 3.10 – 3.12.
- `CONTRIBUTING.md`, `CHANGELOG.md`, and richer `pyproject.toml` metadata.

### Fixed
- `mojo extract` no longer walks every Claude Code project on disk when given a
  single project path.
- `run_filter_api` / `run_filter_api_async` now truncate oversized transcripts
  (keeping the tail) so a session exceeding Haiku's context window does not
  abort the whole `mojo extract` run.

### Removed
- The `MOJO_LLM_BACKEND=claude-code` headless backend. It routed LLM calls
  through `claude -p`, whose billing source (API key vs. subscription) was not
  something the user could reliably predict from the subprocess environment.
  (0.2.0 reintroduces headless `claude -p` as the `claude-cli` backend with
  that ambiguity resolved — the API key is stripped from the child env.)

## [0.1.0] — 2026-04-13

Initial alpha.

### Added
- Unified `mojo` CLI (`init`, `scan`, `extract`, `sync`, `review`, `search`,
  `stats`, `dashboard`, `import-seed`).
- Claude Code `SessionEnd` / `Stop` hook integration.
- Two-stage extraction pipeline: Haiku filter → Sonnet structuring, with
  TF-IDF dedup and token-budget packing.
- Rule-based free scanners: folder scan, git-history scan, past-sessions backfill.
- Web dashboard (FastAPI + React SPA) for reviewing and editing extracted knowledge.
- SQLite-backed storage at `~/.mojo/mojo.db` (override with `MOJO_HOME`).
- Seed import from JSON (`mojo import-seed`).

[0.2.1]: https://github.com/codespermuted/mojo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/codespermuted/mojo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/codespermuted/mojo/releases/tag/v0.1.0
