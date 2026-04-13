# Contributing to Mojo

Thanks for taking the time to contribute! Mojo is a small, focused tool — the bar for changes is clarity and correctness over features.

## Dev setup

```bash
git clone https://github.com/codespermuted/mojo
cd mojo
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the CLI against a scratch home so you never touch your real data:

```bash
export MOJO_HOME=/tmp/mojo-dev
mojo init
mojo scan folder .
```

## Tests

```bash
pytest tests/ -v                      # fast unit tests (no LLM calls)
pytest tests/ -m integration          # integration tests (require ANTHROPIC_API_KEY)
```

All PRs must pass `pytest -m "not integration"` (the same command GitHub Actions runs).

If you add a feature that touches the extraction pipeline, add a fixture under `tests/fixtures/` and a unit test in `tests/test_core.py`.

## Project conventions

These are enforced by review, not tooling:

- **Python 3.10+, type hints.** Match the surrounding code style.
- **All DB access goes through `db_ops.py`.** Don't write SQL in other modules.
- **LLM prompts live in `extract/prompts/` as XML.** Don't inline multi-line prompts in Python.
- **Use `rich.console.Console().print`** for any CLI output, never bare `print`.
- **Hooks must silent-fail.** Anything under `hooks/` must never raise — a broken hook would block Claude Code itself.
- **Token budget matters.** If you add a new packer path, verify it against `serve/packer.py`.

## Commit style

- Use conventional prefixes: `feat:`, `fix:`, `improve:`, `docs:`, `refactor:`, `test:`.
- Keep the subject under 70 characters; put context in the body.
- One logical change per commit. Small, reviewable PRs are merged faster.

## Filing issues

Before filing a bug, please include:

1. `mojo --version` and your Python version
2. The exact command you ran
3. The full error output (redact any file paths or content you don't want public)

For feature ideas, describe the *workflow problem* first, not the proposed solution.

## Scope

Mojo intentionally stays small:

- ✅ Anything that makes the extraction pipeline more accurate, cheaper, or more transparent
- ✅ Better CLI / dashboard UX
- ✅ New scanners for rule-based, no-cost knowledge sources
- ❌ Support for non-Claude-Code transcripts (out of scope for now)
- ❌ Cloud sync / multi-user (Mojo is local-first by design)

When in doubt, open an issue before writing the PR.
