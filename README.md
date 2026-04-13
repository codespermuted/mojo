# Mojo 🔮

> Automatically distill tacit domain knowledge from Claude Code sessions and inject it back into future sessions.

## What is Mojo?

**Problem.** Every Claude Code session produces valuable decisions, corrections, and domain rules — and most of them vanish when the session ends. The next session starts from scratch.

**Solution.** Mojo captures each session through Claude Code hooks, extracts durable knowledge with a two-stage LLM pipeline (Haiku filter → Sonnet structuring), and injects the distilled results into the `CLAUDE.md` and Skill files that future sessions already read.

**Why it's different.**
1. **Zero workflow change** — everything runs behind native Claude Code hooks.
2. **Token-efficient** — a packer keeps the injected context inside a configurable budget and applies an A–F grading system.
3. **Cost-aware by design** — rule-based signals are free; LLM calls only run on sessions that actually contain novel knowledge.

## How Knowledge Grows

Mojo organizes every captured item into one of three tiers. All three feed
into the same SQLite store, the same dashboard, and the same CLAUDE.md
injection, but they answer different questions about *where* the knowledge
came from.

| Tier              | Source              | Method                                 | Default Grade | Cost                |
|-------------------|---------------------|----------------------------------------|---------------|---------------------|
| **T1 · Stored**   | Your expertise      | Seed import, manual dashboard entry    | **B+**        | Free                |
| **T2 · Mined**    | Past work           | Git history / folder scan (rule-based) | **C**         | Free                |
| **T3 · Live**     | Ongoing work        | Claude Code session hooks + LLM        | **A–B**       | ~$0.04 / session    |

Over time, T2 and T3 items get approved, reused, and cross-linked — which
promotes them up the [A–F grade](#evidence-based-grading-af) hierarchy. The
three tiers compound: every session makes the next one smarter without any
single source being mandatory.

## Quick Start

Pick whichever install path matches your environment — all three end up at
the same place (a local `mojo.db` plus Claude Code hooks).

### Option A — Clone from GitHub (recommended)

```bash
git clone https://github.com/codespermuted/mojo.git
cd mojo
pip install -e .

python init.py                                   # creates DB + registers hooks
python dashboard/server.py                       # http://localhost:8765
```

### Option B — Run against an existing local checkout

If you already have the source somewhere on disk (e.g. a ZIP download, a
worktree, or a symlinked copy):

```bash
cd /path/to/mojo        # wherever the source lives
pip install -e .        # or: pip install -r requirements.txt
python init.py
```

### Option C — Keep data outside the source tree

Mojo's storage location is controlled by `MOJO_HOME`. Everything writable
(the SQLite DB, generated skills, hook scripts) lives there, so you can put
the source read-only in `/opt/mojo` and the data in `~/.mojo`, on an
external drive, or in a per-project folder:

```bash
# Default: ~/.mojo
python init.py

# External drive / NAS
export MOJO_HOME=/mnt/nas/mojo
python init.py

# Per-project isolation (keeps one project's knowledge out of the global store)
MOJO_HOME=./.mojo python init.py
```

Once `init.py` has run, hooks fire automatically on the next Claude Code
session — no further setup is required.

## Adding Knowledge

Mojo is designed so that every way of adding knowledge ends up in the same
SQLite store and the same dashboard. You can mix and match freely.

| Source                    | Command / Action                                    | Tier |
|---------------------------|-----------------------------------------------------|------|
| **Git history**           | `python scan.py git /path/to/project`               | T2   |
| **Folder scan**           | `python scan.py folder /path/to/project`            | T2   |
| **Past Claude sessions**  | `python scan.py sessions`                           | T3   |
| **Live Claude sessions**  | automatic (hooks installed by `init.py`)            | T3   |
| **Hand-written seed**     | `python import_seed.py seeds/seed_knowledge.json`   | T1   |
| **Dashboard → + Add**     | click **+ Add** in the top bar of the dashboard     | T1   |
| **GitHub repo (remote)**  | `git clone` it first, then `python scan.py git <dir>` | T2   |

A quick tour of the typical additions:

```bash
# Scan a project you already have locally
python scan.py git   ~/code/my-service
python scan.py folder ~/code/my-service

# Scan a public repo without cloning it into the Mojo tree
git clone --depth 200 https://github.com/org/repo /tmp/repo
python scan.py git /tmp/repo

# Import a curated seed JSON
python import_seed.py seeds/seed_knowledge.json

# Sync the result into any project's CLAUDE.md
python -m serve.sync --project ~/code/my-service
```

Every added item shows up in the dashboard, where you can approve, edit,
structure, or archive it. Mutating actions (archive, structure) emit an
**undo toast** so accidental edits are one click away from being reverted.

## Deployment

Mojo is a local-first tool — there is no cloud component. "Deploying" it
means one of:

1. **Personal laptop** — run `python dashboard/server.py` whenever you want
   to browse your knowledge base, or leave it running in the background.
2. **Always-on box (home server / NAS)** — point `MOJO_HOME` at a persistent
   path and run the dashboard under `systemd`, `launchd`, or `tmux`:

   ```bash
   MOJO_HOME=/srv/mojo python dashboard/server.py --host 0.0.0.0 --port 8765 --no-browser
   ```
3. **Per-project sidecar** — keep `MOJO_HOME=./.mojo` inside each repo so
   every project has its own isolated knowledge store and CLAUDE.md
   injections. Useful when you don't want cross-project knowledge bleed.

Back up `$MOJO_HOME/mojo.db` (it's a plain SQLite file) to snapshot your
entire knowledge base.

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
T1 · Stored ──┐
  seed import │
  manual entry│
              │
T2 · Mined  ──┼──► SQLite store ──► Packer ──► CLAUDE.md / SKILL.md
  git scan    │   (dedup, grades)  (budget)         │
  folder scan │         ▲                            ▼
              │         │                  Next Claude Code session
T3 · Live   ──┘         │                            │
  session hook + LLM    │                            │
  (Haiku filter +       └────── usage feedback ──────┘
   Sonnet structure)           (reuses, approvals,
                                cross-references)
```

The evidence-based grader sits between the store and the packer, so only
items that have actually earned trust make it into the final token budget.

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
