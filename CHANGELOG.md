# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Previous unreleased entries
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
  Use `ANTHROPIC_API_KEY` with the default backend, which has transparent
  per-call cost tracking in `extraction_costs`.

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

[Unreleased]: https://github.com/codespermuted/mojo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/codespermuted/mojo/releases/tag/v0.1.0
