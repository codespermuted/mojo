# Mojo 🔮

> A local-first knowledge pipeline that turns every Claude Code session into
> durable, reusable domain expertise — and injects it back into the next
> session automatically.

## The Problem

Every Claude Code session produces valuable decisions, corrections, and
domain rules:

- *"No — in our environment we use B instead of A, because…"*
- *"This framework has an N+1 trap here, always prefetch…"*
- *"Don't revert that, it was failing because of the cache key, not the query."*

And then the session ends, the context window is gone, and the next session
starts from scratch. The knowledge is in the transcript, but nothing
consumes it.

## The Promise

Mojo captures every session through Claude Code's native hooks, distills
the durable knowledge with a two-stage LLM pipeline (Haiku filter → Sonnet
structure), grades it by evidence, and writes only the items that have
earned their keep into the `CLAUDE.md` and `SKILL.md` files that future
sessions already read.

Your workflow doesn't change. Claude just stops forgetting.

## Why Mojo, not `.cursorrules` or a RAG index

| Concern                     | Hand-maintained rules file                    | Generic RAG                           | **Mojo**                                                                              |
|-----------------------------|-----------------------------------------------|---------------------------------------|---------------------------------------------------------------------------------------|
| **Capture effort**          | Manual — interrupts flow                      | None, but dumps everything            | **Zero** — native Claude Code hooks fire silently in the background                   |
| **Token budget**            | Grows unbounded, pollutes attention           | Every retrieval competes for tokens   | **Packer** with a hard budget + A–F grading, knapsack-fills only what's worth it      |
| **Signal quality**          | Whatever you remember to write down           | Retrieves anything superficially similar | **Evidence-based grades** (A–F) — items earn promotion through reuse & approval     |
| **Detail vs. index split**  | Everything in one file                        | Chunks with no hierarchy              | **`CLAUDE.md` holds summaries; `SKILL.md` holds the receipts** — read only when needed |
| **Bootstrap a new project** | Empty on day one                              | Needs embedding pipeline              | **Free** rule-based git/folder scan bootstraps a baseline with zero API calls         |
| **Review & edit**           | Open the file, hope you remember where        | Not really a thing                    | **Web dashboard** with list + graph views, inline edit, undo, multi-select filtering  |

## The Five Pillars

### 1. Zero-friction capture

`mojo init` registers two Claude Code hooks (`SessionEnd`, `Stop`). After
that, Mojo records every session transcript to its SQLite store in the
background. You never stop to write a rule down.

### 2. Token-efficient injection

`serve/packer.py` treats the `CLAUDE.md` block as a knapsack: given a token
budget (default ~3,000), it picks the items that maximize expected value
based on grade, recency, reuse, and domain relevance. Your CLAUDE.md
doesn't grow unbounded, and the model's attention isn't diluted.

### 3. Evidence-based grading (A–F)

Grades are decided by *evidence*, not confidence scores:

| Grade | Label        | Criterion                                                       |
|-------|--------------|-----------------------------------------------------------------|
| **A** | Verified     | ≥ 2 related sources, **or** `usage ≥ 3` AND approved            |
| **B** | Corroborated | Has explicit reasoning **and** approved                         |
| **C** | Reported     | Auto-extracted, single source, not yet reviewed                 |
| **D** | Inferred     | Weak signal — low confidence, or no reasoning AND not approved  |
| **F** | Contested    | `confidence < 0.3`, or never used for > 180 days                |

First-match wins. F-grade items are **excluded from `CLAUDE.md` entirely**,
so a single bad extraction can't poison future sessions.

### 4. CLAUDE.md summaries, SKILL.md receipts

Mojo writes a short, high-grade index into the project's `CLAUDE.md`, and
offloads the long-form evidence (code snippets, original reasoning, linked
details) into per-domain `SKILL.md` files. Claude Code reads them *only*
when the task actually needs the details — so your global context stays
small and your skill files stay expressive.

### 5. Free git-history scanning

`mojo scan git /path/to/project` uses rule-based extractors (`fix:` →
debug playbook, `revert:` → anti-pattern, keywords like *replace* /
*migrate* / *never* → architecture decision) to bootstrap the store from a
repo's history. **Zero API calls, zero cost.** Promotions to B-grade happen
later when you run the LLM pipeline.

## Quick Start

All three install paths end up at the same place — a local `mojo.db` plus
Claude Code hooks. Pick the one that matches your environment.

### Option A — GitHub clone (recommended)

```bash
git clone https://github.com/codespermuted/mojo.git
cd mojo
pip install -e .

mojo init                 # create ~/.mojo, register hooks
mojo dashboard            # http://localhost:8765
```

### Option B — Run against an existing local checkout

If you already have the source on disk (ZIP download, worktree, symlink):

```bash
cd /path/to/mojo
pip install -e .          # or: pip install -r requirements.txt
mojo init
```

### Option C — Keep data outside the source tree

`MOJO_HOME` controls every writable path (SQLite DB, generated skills,
hooks). The source tree can stay read-only in `/opt/mojo` while data lives
wherever you want:

```bash
# Default: ~/.mojo
mojo init

# External drive / NAS
export MOJO_HOME=/mnt/nas/mojo
mojo init

# Per-project isolation — keeps this project's knowledge out of the global store
MOJO_HOME=./.mojo mojo init
```

Once `mojo init` has run, hooks fire automatically on the next Claude Code
session. No further setup is required.

## The `mojo` CLI

Every command is a subcommand of a single entrypoint. Run
`mojo <command> --help` for command-specific flags.

```
mojo init          Create ~/.mojo, copy config, register Claude Code hooks
mojo dashboard     Run the web dashboard (http://localhost:8765)
mojo scan          Rule-based git / folder / sessions scan (free)
mojo extract       Run the LLM extraction pipeline (Haiku → Sonnet)
mojo sync          Write CLAUDE.md / SKILL.md into a project
mojo review        Approve / edit extracted items from the terminal
mojo search        Full-text search across the knowledge store
mojo stats         Show store statistics and extraction cost
mojo import-seed   Bulk-import a seed knowledge JSON file
```

### Typical workflows

```bash
# Bootstrap a new project from its git history (free, rule-based)
mojo scan git   ~/code/my-service
mojo scan folder ~/code/my-service

# Scan a public repo without adding it to your tree
git clone --depth 200 https://github.com/org/repo /tmp/repo
mojo scan git /tmp/repo

# Curated seed knowledge you already wrote
mojo import-seed seeds/seed_knowledge.json

# Manual entry via the dashboard — click "+ Add" in the top bar
mojo dashboard

# Process pending Claude Code sessions with the LLM pipeline
export ANTHROPIC_API_KEY=sk-ant-...
mojo extract

# Inject graded knowledge into a project's CLAUDE.md (and SKILL.md)
mojo sync --project ~/code/my-service --skill

# Audit what you have
mojo stats
mojo search "n+1 query"
```

## Adding Knowledge

Every source feeds the same SQLite store, is visible in the same
dashboard, and competes on the same A–F grades. Mix and match freely.

| Source                    | Command / Action                                    | Tier |
|---------------------------|-----------------------------------------------------|------|
| **Git history**           | `mojo scan git /path/to/project`                    | T2   |
| **Folder scan**           | `mojo scan folder /path/to/project`                 | T2   |
| **Past Claude sessions**  | `mojo scan sessions`                                | T3   |
| **Live Claude sessions**  | automatic (hooks installed by `mojo init`)          | T3   |
| **Hand-written seed**     | `mojo import-seed seeds/seed_knowledge.json`        | T1   |
| **Dashboard → + Add**     | click **+ Add** in the top bar of the dashboard     | T1   |
| **Remote GitHub repo**    | `git clone …` then `mojo scan git <dir>`            | T2   |

The three tiers exist to answer *where did this come from*, not to gate
trust — trust is tracked separately via the A–F grade.

| Tier          | Source           | Method                                | Default grade | Cost           |
|---------------|------------------|---------------------------------------|---------------|----------------|
| **T1 Stored** | Your expertise   | Seed import or manual dashboard entry | **B+**        | Free           |
| **T2 Mined**  | Past work        | Rule-based git / folder scan          | **C**         | Free           |
| **T3 Live**   | Ongoing work     | Claude Code hooks + LLM extraction    | **A–B**       | ~$0.04/session |

## The Dashboard

`mojo dashboard` opens a single-file React SPA served by FastAPI at
`http://localhost:8765`. Features:

- **List + graph views** — cards grouped by domain, or a d3 force-directed
  graph with hierarchical summary/detail orbits and shared-tag edges
- **Multi-select sidebar filters** — domains, types, status, and tiers;
  click to add, click again to deselect, `clear` per section, and a
  chip bar showing every active filter
- **Collapsible DOMAINS tree** — click a major topic to filter by it;
  click the `▸` chevron to open its subtopics
- **Graph → list drill-down** — clicking a summary node in the graph
  switches to list view, expands the summary card, and auto-unfolds
  every linked detail (full content, reasoning, tags) in the Evidence
  panel with `expand all` / `collapse all` toggles
- **Undo toast** — mutating ops (archive, structure) surface an
  `UNDO` button for ~5 seconds; reverts both run against the backend
- **A–F grade pills** with bar charts and color coding
- **Inline edit, approve, archive, delete, structure, refine** — all
  mutations persist to the same SQLite store `mojo sync` reads from
- **Light / dark theme toggle** — persisted in `localStorage`
- **No build step** — React, ReactDOM, Babel-standalone, and d3 are
  loaded from CDN

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

## Storage Layout

All data stays **local**. Default location is `~/.mojo/`; override with
the `MOJO_HOME` environment variable.

```
$MOJO_HOME/
├── mojo.db          # SQLite (knowledge, sessions, extraction_costs)
├── config.yaml      # settings (budgets, thresholds)
├── hooks/           # Claude Code hook scripts
└── skills/          # auto-generated SKILL.md files
```

Back up `$MOJO_HOME/mojo.db` to snapshot the entire knowledge base.

## Deployment

Mojo is local-first — there is no cloud component. "Deploying" means one
of:

1. **Personal laptop** — run `mojo dashboard` whenever you want to browse,
   or leave it running in the background.
2. **Always-on box (home server / NAS)** — put `MOJO_HOME` on a persistent
   path and run the dashboard under `systemd`, `launchd`, or `tmux`:

   ```bash
   MOJO_HOME=/srv/mojo mojo dashboard --host 0.0.0.0 --port 8765 --no-browser
   ```
3. **Per-project sidecar** — keep `MOJO_HOME=./.mojo` inside each repo so
   every project has its own isolated store and CLAUDE.md injections.
   Useful when you don't want cross-project knowledge bleed.

## Cost

- Signal detection and git scanning: **free** (rule-based, no API calls)
- Haiku filter: ~$0.001 / session
- Sonnet structuring: ~$0.005 / candidate
- Typical monthly total: **$1–5** at 5–10 sessions / day

All costs are tracked per stage in the `extraction_costs` SQLite table.
Set `costs.monthly_budget_usd` in `config.yaml` to hard-stop extraction
when a budget is exceeded.

## With or Without an API Key

Mojo works without an Anthropic API key. The key only unlocks the
LLM-powered structuring pipeline; everything else — scanning, storage,
dashboard, CLAUDE.md generation — runs for free.

| Feature                                  | Without API Key        | With API Key      |
|------------------------------------------|:----------------------:|:-----------------:|
| Git history scan                         | ✅ Rule-based, Grade C | ✅ Same           |
| Folder scan                              | ✅ Full                | ✅ Same           |
| Seed import                              | ✅ Full                | ✅ Same           |
| Dashboard (view / edit / add / delete)   | ✅ Full                | ✅ Same           |
| CLAUDE.md generation                     | ✅ Full                | ✅ Same           |
| Graph visualization                      | ✅ Full                | ✅ Same           |
| Session auto-capture (hooks)             | ✅ Registration only   | ✅ Same           |
| **Session → structured knowledge**       | ❌                     | ✅ Grade A–B      |
| **Cost**                                 | **$0**                 | **~$0.04/session** |

To enable LLM extraction:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
mojo extract
```

## Demo: Open-Source Repositories

To show what Mojo produces on a realistic corpus with no hand-crafted
seed data, we ran the rule-based git scanner on three popular open-source
projects.

- 200 most recent non-merge commits per repo
- Rule-based extraction only (no LLM calls)
- **Total API cost: $0.00**
- Environment: Ubuntu 24, Python 3.12

| Repository          | ~Stars | Commits | Candidates | Saved | Dup Skipped | Type breakdown                      |
|---------------------|-------:|--------:|-----------:|------:|------------:|-------------------------------------|
| `fastapi/fastapi`   |   82k+ |     200 |          6 |     6 |           0 | arch:5 · rule:1                     |
| `pydantic/pydantic` |   23k+ |     200 |         60 |    49 |          11 | debug:40 · arch:16 · anti:3 · rule:1 |
| `Textualize/rich`   |   52k+ |     200 |         23 |    23 |           0 | debug:18 · arch:5                   |
| **Total**           |        |     600 |         89 |    78 |          11 |                                     |

Bracketed letters in the output are Mojo's evidence-based grade
(`A Verified` → `F Contested`). Items scanned from git histories start
at **C · Reported** and get promoted as they accumulate human approval,
reasoning, or cross-references.

> **Note:** Rule-based extraction produces Grade C items by default.
> `mojo extract` upgrades them to Grade B (Corroborated) with structured
> reasoning.

### LLM structuring cost (one session benchmark)

Rule-based scanning is free. When you run the LLM structuring pipeline
on a full Claude Code session, the observed cost for a single session
with three structured candidates was:

| Stage            | Model   | Tokens (in/out) | Cost        |
|------------------|---------|----------------:|------------:|
| Filter           | Haiku   | 1,049 / 283     | $0.0006     |
| Structure × 3    | Sonnet  | 5,472 / 1,303   | $0.0360     |
| **Session total**|         |                 | **$0.0370** |

## Requirements

- Python 3.10+
- `ANTHROPIC_API_KEY` (only for `mojo extract`; scanning and everything
  else run without it)
- Claude Code installed (so hooks can be registered; optional for the
  dashboard and git scanning)

## Roadmap

- [ ] End-to-end auto-pipeline: session hook → `extract` → `sync` as a
      single trigger
- [ ] Seed vs. LLM version coexistence — auto-replace shorter seed
      entries when a richer LLM extraction supersedes them
- [ ] "Sync now" button in the dashboard
- [ ] Multi-user knowledge merging for team-shared stores
- [ ] Pluggable extractors (issue trackers, other code hosts)

## Disclaimer

Mojo is an experimental tool for personal knowledge management around
Claude Code. It extracts text from commit messages and session transcripts
using heuristics and LLM calls; **outputs may be incomplete, incorrect,
or out of date** and should always be reviewed before acting on them.

The project is **not affiliated with, sponsored by, or endorsed by**
Anthropic, Claude Code, Modular's [Mojo programming language](https://www.modular.com/mojo),
or any of the open-source projects referenced in the demo section. All
third-party names, logos, and trademarks belong to their respective owners
and are used here for descriptive purposes only.

You are responsible for ensuring that any use of Mojo complies with the
license, privacy, and data-handling requirements of the systems it
connects to. Review what is captured before sharing a Mojo database or
generated artifacts.

## License

MIT
