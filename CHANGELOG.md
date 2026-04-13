# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
