# Mojo 🔮

> Automatically distill tacit domain knowledge from Claude Code sessions and inject it back into future sessions.

## What is Mojo?

**Problem.** Every Claude Code session produces valuable decisions, corrections, and domain rules — and most of them vanish when the session ends. The next session starts from scratch.

**Solution.** Mojo captures each session through Claude Code hooks, extracts durable knowledge with a two-stage LLM pipeline (Haiku filter → Sonnet structuring), and injects the distilled results into the `CLAUDE.md` and Skill files that future sessions already read.

**Why it's different.**
1. **Zero workflow change** — everything runs behind native Claude Code hooks.
2. **Token-efficient** — a packer keeps the injected context inside a configurable budget and applies an A–F grading system.
3. **Cost-aware by design** — rule-based signals are free; LLM calls only run on sessions that actually contain novel knowledge.

## Quick Start

```bash
git clone https://github.com/<your-fork>/mojo.git
cd mojo
pip install -e .

# 1. Initialize (creates DB, installs Claude Code hooks)
python init.py

# 2. (Optional) Seed from an existing git project
python scan.py git /path/to/your/project

# 3. Sync distilled knowledge into a project's CLAUDE.md
python -m serve.sync --project /path/to/your/project

# 4. Use Claude Code normally — new knowledge accumulates automatically
```

## Demo: Open-Source Repositories

To show what Mojo produces on a realistic corpus with no hand-crafted seed data,
we ran the rule-based git scanner on three popular open-source projects.

### Conditions
- 200 most recent non-merge commits per repo
- Rule-based extraction only (no LLM calls)
- **Total API cost: $0.00**
- Environment: Ubuntu 24, Python 3.12

### Results

| Repository          | ~Stars | Commits | Candidates | Saved | Dup Skipped | Type breakdown |
|---------------------|-------:|-------:|-----------:|------:|------------:|---------------|
| `fastapi/fastapi`   |   82k+ |    200 |          6 |     6 |           0 | arch:5 · rule:1 |
| `pydantic/pydantic` |   23k+ |    200 |         60 |    49 |          11 | debug:40 · arch:16 · anti:3 · rule:1 |
| `Textualize/rich`   |   52k+ |    200 |         23 |    23 |           0 | debug:18 · arch:5 |
| **Total**           |        |    600 |         89 |    78 |          11 | |

The scanner classifies commits with simple rules:
- `revert:` / `undo:` → **anti_pattern**
- `fix:` / `hotfix:` → **debug_playbook**
- keywords like *replace*, *migrate*, *instead of*, *never*, *don't* → **architecture_decision**

### Sample extractions

```
project/pydantic
  ⚠️ [B] Revert "Box large fields in CombinedValidator/CombinedSerializer…"
  ⚠️ [B] Revert "Apply temporary fix for documentation CI (#12699)"
  -  [C] Fix model equality when using runtime `extra` configuration

project/rich
  -  [C] fix inline code in table cells
  -  [C] don't test 3.8

project/fastapi
  -  [C] Refactor logic to handle OpenAPI and Swagger UI escaping data
```

The bracketed letter is Mojo's evidence-based grade
(A Verified → F Contested). Items scanned from git histories start at
**C · Reported** and are promoted as they accumulate human approval,
reasoning, or cross-references.

> **Note:** Rule-based extraction produces Grade C items by default. Running
> the LLM pipeline (`python -m extract.pipeline`) upgrades them to Grade B
> (Corroborated) with structured reasoning. See
> [With or Without an API Key](#with-or-without-an-api-key).

### LLM Structuring Cost (one session benchmark)

Rule-based scanning is free. When you run the LLM structuring pipeline on a
full Claude Code session, the observed cost for a single session with three
structured candidates was:

| Stage            | Model   | Tokens (in/out) | Cost     |
|------------------|---------|----------------:|---------:|
| Filter           | Haiku   | 1,049 / 283     | $0.0006  |
| Structure × 3    | Sonnet  | 5,472 / 1,303   | $0.0360  |
| **Session total**|         |                 | **$0.0370** |

That's roughly **$1–5 / month** at 5–10 sessions per day.

### CLAUDE.md snippet auto-generated from the demo

```markdown
<!-- MOJO:START - Auto-managed by Mojo. Do not edit manually. -->
## Domain Knowledge (Mojo)

### project/pydantic
⚠️ [B] **Revert "Box large fields in CombinedValidator/…"** — reverted #12985
-  [C] **Fix model equality when using runtime `extra` config** — #13062

### project/rich
-  [C] **fix inline code in table cells**
<!-- MOJO:END -->
```

> ⚠️ **Disclaimer.** These three repositories are used **only** as a reproducible
> public corpus for demonstrating Mojo's rule-based extractor. The extracted
> items are surfaced *as is* from public commit messages. Mojo is **not
> affiliated with, endorsed by, or a representative of** FastAPI, Pydantic, or
> Textualize/Rich. No claim is made about the correctness, applicability, or
> official status of any extracted item — commit messages are a noisy source
> and rule-based extraction is approximate. Please treat demo output as a
> format sample, not as engineering advice.

## Architecture

```
Claude Code session
        │
        ▼
Native hooks (SessionEnd, Stop)          ← zero workflow change
        │
        ▼
JSONL parser + correction-signal detection   (free, rule-based)
        │
        ▼
Haiku filter      ~$0.001 / session          (noise removal)
        │  (only if signal present)
        ▼
Sonnet structuring ~$0.005 / candidate       (title, content, reasoning, tags)
        │
        ▼
SQLite store + TF-IDF dedup
        │
        ▼
Token-budget packer (A–F grades, type weights, recency)
        │
        ▼
CLAUDE.md + SKILL.md files
        │
        ▼
Next Claude Code session reads the injected knowledge automatically
```

## Storage

All data stays **local**. Default location is `~/.mojo/`; override with the
`MOJO_HOME` environment variable.

```bash
# Default
python init.py

# External drive / NAS
export MOJO_HOME=/mnt/nas/mojo
python init.py

# Per-project isolation
MOJO_HOME=./local-mojo python init.py
```

Files that Mojo writes:

```
$MOJO_HOME/
├── mojo.db          # SQLite (knowledge, sessions, costs)
├── config.yaml      # settings (budgets, thresholds)
├── hooks/           # Claude Code hook scripts
└── skills/          # auto-generated SKILL.md files
```

## Commands

```bash
# Discover knowledge sources
python scan.py folder .            # inspect a project for existing signals
python scan.py git .               # rule-based extraction from git history
python scan.py git . --dry-run     # preview only
python scan.py sessions            # backfill from past Claude Code sessions

# Extract & sync
python -m extract.pipeline         # process pending sessions (Haiku → Sonnet)
python -m serve.sync --project .   # write CLAUDE.md
python -m serve.sync --skill       # also generate SKILL.md files

# Manage
python review.py                   # approve / edit extracted items
python search.py "keyword"         # search knowledge
python stats.py                    # overall stats + cost

# Web dashboard
python dashboard/server.py         # http://localhost:8765
```

## Dashboard

A single-file React SPA (served by FastAPI) provides a visual interface for
reviewing, editing, and exploring your knowledge base.

```bash
python dashboard/server.py
# → opens http://localhost:8765 in your browser
```

Features:
- **List and graph views** — cards grouped by domain, or a d3 force-directed
  graph of related / shared-tag links
- **A–F grading system** — letter pills with color coding
- **Inline edit, add, delete, approve, archive** — all mutations persist to the
  same SQLite store that `serve.sync` reads
- **Light / dark theme toggle** — persisted in `localStorage`
- **Domain tree + type sidebar filters**, full-text search over title / content
  / tags
- **No build step** — React, ReactDOM, Babel-standalone, and d3 are loaded from
  CDN

## Knowledge Sources

| Source             | Command            | Description |
|--------------------|--------------------|-------------|
| Git history        | `scan.py git`      | `revert:` → anti_pattern, `fix:` → debug_playbook, decision keywords → architecture_decision |
| Claude Code hooks  | automatic          | `SessionEnd` + `Stop` hooks register each session for extraction |
| Folder scan        | `scan.py folder`   | Surfaces existing `CLAUDE.md`, `README.md`, config files, past session transcripts |
| Seed JSON          | `import_seed.py`   | Bulk-import a hand-written knowledge file |

## Cost

- Signal detection and git scanning: **free** (rule-based)
- Haiku filter: ~$0.001 / session
- Sonnet structuring: ~$0.005 / candidate
- Typical monthly total: **$1–5** (5–10 sessions / day)

All costs are tracked per stage in the `extraction_costs` SQLite table. Set
`costs.monthly_budget_usd` in `config.yaml` to hard-stop extraction when a
budget is exceeded.

## Evidence-Based Grading (A–F)

Each knowledge item is graded by **evidence quality**, not by an arbitrary
confidence threshold. The criteria below are evaluated top-down — the first
rule that matches wins.

| Grade | Label        | Criterion |
|-------|--------------|-----------|
| **A** | Verified     | ≥ 2 related sources, **or** (`usage ≥ 3` AND approved) |
| **B** | Corroborated | Has explicit reasoning **and** approved |
| **C** | Reported     | Auto-extracted, single source, not yet reviewed |
| **D** | Inferred     | Weak signal: low confidence, or no reasoning AND not approved |
| **F** | Contested    | `confidence < 0.3`, or never used for > 180 days |

The rationale is that an item's trustworthiness should reflect whether humans
or other systems have corroborated it — not just how confident the extractor
felt at the moment. **F-grade items are excluded from `CLAUDE.md` entirely.**

## With or Without an API Key

Mojo works without an Anthropic API key. The API key only unlocks the
LLM-powered structuring pipeline; everything else — scanning, storage,
dashboard, CLAUDE.md generation — runs for free.

| Feature                                  | Without API Key | With API Key |
|------------------------------------------|:---------------:|:------------:|
| Git history scan                         | ✅ Rule-based, Grade C | ✅ Same |
| Folder scan                              | ✅ Full         | ✅ Same |
| Seed import                              | ✅ Full         | ✅ Same |
| Dashboard (view / edit / add / delete)   | ✅ Full         | ✅ Same |
| CLAUDE.md generation                     | ✅ Full         | ✅ Same |
| Graph visualization                      | ✅ Full         | ✅ Same |
| Session auto-capture (hooks)             | ✅ Registration only | ✅ Same |
| **Session → structured knowledge**       | ❌              | ✅ Grade A–B |
| **Cost**                                 | **$0**          | **~$0.04 / session** |

To enable LLM extraction:

```bash
export ANTHROPIC_API_KEY=your-key-here
```

## Requirements

- Python 3.10+
- `ANTHROPIC_API_KEY` (only for the LLM extraction pipeline; scanning is free)
- Claude Code installed (so that hooks can be registered; optional for the
  dashboard and for git scanning)

## Known Issues & Roadmap

- [ ] End-to-end auto-pipeline: Claude Code session hook → extract → sync on a single trigger
- [ ] Seed vs. LLM version coexistence — auto-replace shorter seed entries when a more detailed LLM extraction supersedes them
- [ ] "Sync now" button in the dashboard
- [ ] Multi-user knowledge merging for team shared stores
- [ ] Pluggable extractors (other code hosting platforms, issue trackers)

## Disclaimer

Mojo is an experimental tool for personal knowledge management around Claude
Code. It extracts text from commit messages and session transcripts using
heuristics and LLM calls; **outputs may be incomplete, incorrect, or
out-of-date** and should always be reviewed before acting on them.

The project is **not affiliated with, sponsored by, or endorsed by** Anthropic,
Claude Code, or any of the open-source projects referenced in the demo
section. All third-party names, logos, and trademarks belong to their
respective owners and are used here for descriptive purposes only.

You are responsible for ensuring that any use of Mojo complies with the
license, privacy, and data-handling requirements of the systems it connects
to. Review what is captured before sharing a Mojo database or generated
artifacts.

## License

MIT
