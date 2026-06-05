# Mojo 🔮

> A local-first tacit-knowledge reference layer that turns Claude/Codex
> sessions, commits, notes, and decisions into scoped advisory context for
> the next task.

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

Mojo captures work evidence through local hooks and backfill commands
(both **Claude Code and Codex** sessions), distills durable tacit
knowledge with a two-stage LLM pipeline (cheap filter → strong
structurer), and keeps quality separate from applicability scope.

Three things make the loop sustainable:

1. **Zero marginal cost by default.** The pipeline runs on headless
   `claude -p` (your subscription), not an API key. `codex exec` and the
   Anthropic API remain available as backends.
2. **Knowledge lives as markdown in an Obsidian vault.** One claim = one
   note; reviewing = editing frontmatter. The knowledge is visible where
   you already read and think, not locked in SQLite.
3. **Generalization is evidence, not filing.** Every claim starts
   project-scoped; re-observing it in a second project records evidence,
   and only cross-project support suggests promotion. A counterexample
   sharpens `does_not_apply_when` instead of deleting the claim.

Your workflow stays human-controlled: Mojo surfaces what matters, why it
was selected, where it applies, and where it should not be generalized.

## Reference Layer, Not Auto-Injection

Mojo's default project workflow is attach/detach:

```bash
mojo attach --project ~/code/my-service
mojo refresh --project ~/code/my-service --task "debug auth cache"
mojo status --project ~/code/my-service
mojo detach --project ~/code/my-service
```

`mojo refresh` generates `MOJO.md` as advisory context. It does not edit
`AGENTS.md`, `CLAUDE.md`, `SKILL.md`, or other human-authored instruction
files. Legacy `mojo sync` remains an explicit command for users who
deliberately want to write approved knowledge into Claude-facing files.

## Why Mojo, not `.cursorrules` or a RAG index

| Concern                     | Hand-maintained rules file                    | Generic RAG                           | **Mojo**                                                                              |
|-----------------------------|-----------------------------------------------|---------------------------------------|---------------------------------------------------------------------------------------|
| **Capture effort**          | Manual — interrupts flow                      | None, but dumps everything            | **Zero** — native Claude Code hooks fire silently in the background                   |
| **Token budget**            | Grows unbounded, pollutes attention           | Every retrieval competes for tokens   | **Packer** with a hard budget + A–F grading, knapsack-fills only what's worth it      |
| **Signal quality**          | Whatever you remember to write down           | Retrieves anything superficially similar | **Evidence-based grades** (A–F) — items earn promotion through reuse & approval     |
| **Detail vs. index split**  | Everything in one file                        | Chunks with no hierarchy              | **`MOJO.md` holds scoped advisory context; DB rows hold the receipts** |
| **Bootstrap a new project** | Empty on day one                              | Needs embedding pipeline              | **Free** rule-based git/folder scan bootstraps a baseline with zero API calls         |
| **Review & edit**           | Open the file, hope you remember where        | Not really a thing                    | **Web dashboard** with list + graph views, inline edit, undo, multi-select filtering  |

## The Five Pillars

### 1. Zero-friction capture

`mojo init` registers two Claude Code hooks (`SessionEnd`, `Stop`). After
that, every session is registered **and auto-extracted in the background**
(detached process, logged to `~/.mojo/logs/auto-extract.log`) — the
default backend is headless `claude -p`, so this costs nothing and needs
no API key. You never stop to write a rule down. Set
`extraction.auto_extract: false` to go back to manual `mojo extract`.

### 2. Scoped advisory retrieval

`advisory.py` maps the current task, project path, files, and search terms
to relevant tacit knowledge. The generated `MOJO.md` includes scope,
applicability warnings, source lineage, and review state so the agent can
use it as context without treating raw evidence as a system instruction.

### 3. Evidence-based grading (A–F)

Grades are decided by *evidence*, not confidence scores:

| Grade | Label        | Criterion                                                       |
|-------|--------------|-----------------------------------------------------------------|
| **A** | Verified     | ≥ 2 related sources, **or** `usage ≥ 3` AND approved            |
| **B** | Corroborated | Has explicit reasoning **and** approved                         |
| **C** | Reported     | Auto-extracted, single source, not yet reviewed                 |
| **D** | Inferred     | Weak signal — low confidence, or no reasoning AND not approved  |
| **F** | Contested    | `confidence < 0.3`, or never used for > 180 days                |

First-match wins. F-grade items are excluded from advisory output and
legacy sync targets, so a single bad extraction can't poison future
sessions.

### 4. Quality and scope are separate

Every item tracks both quality and applicability: `taxon`, `scope`,
`applies_when`, `does_not_apply_when`, `evidence_level`,
`promotion_state`, `source_lineage`, `counterexamples`, and conflicts.
This prevents project-local incidents from silently becoming universal
"Always/Never" rules.

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
```

Once `mojo init` has run, hooks fire automatically on the next Claude Code
session. No further setup is required.

### Per-project isolation

Run `mojo init` **once** globally — that registers the Claude Code
hooks machine-wide. To scope a specific project's sessions to its
own store, create a per-project `.mojo` sidecar next to the repo:

```bash
cd ~/code/my-service
MOJO_HOME=./.mojo mojo init --skip-hooks   # creates ./.mojo/mojo.db
echo ".mojo/" >> .gitignore
```

From then on, Claude Code sessions launched with a cwd inside
`~/code/my-service` (or any subdirectory) write to
`~/code/my-service/.mojo/mojo.db`. Sessions from any other cwd fall
back to the global `~/.mojo/mojo.db`. The session hook walks up from
the session's cwd looking for the nearest `.mojo/mojo.db`, so nested
projects get their sessions routed to the innermost sidecar rather
than the enclosing one.

`--skip-hooks` matters: you've already registered the hooks globally
in the first `mojo init`, and the runtime cwd-based resolution in
`hooks/_resolve.py` is what makes the per-project routing work. A
second global hook registration from inside the project would just
churn `~/.claude/settings.json` without adding anything.

> **Past sessions aren't auto-imported.** The hook captures only
> *future* sessions. To backfill existing transcripts for a specific
> project, run `mojo scan sessions --project /path/to/project` — it
> defaults to the current working directory and only touches that
> project's Claude Code transcript dir. Pass `--project all` to opt
> into the global backfill behaviour explicitly.

### Contributing

Use `scripts/dev-install.sh` instead of a plain `pip install -e .`.
Hatch's `force-include` (needed for the published wheel) writes
static copies of the root-level flat modules into site-packages at
install time, which silently break live editing until the next
reinstall. The script runs the editable install and then replaces
those copies with symlinks back to the source tree, so edits to
`scan.py` / `init.py` / `db_ops.py` / etc. propagate immediately.

## The `mojo` CLI

Every command is a subcommand of a single entrypoint. Run
`mojo <command> --help` for command-specific flags.

```
mojo init          Create ~/.mojo, copy config, register Claude Code hooks
mojo attach        Attach Mojo advisory metadata to a project
mojo detach        Detach Mojo advisory metadata from a project
mojo status        Show attachment, DB, and advisory-file status
mojo refresh       Regenerate advisory MOJO.md for the current task
mojo vault         Sync knowledge with the Obsidian vault (md ↔ DB)
mojo companion     Quiet sidecar/check intervention layer
mojo dashboard     Run the web dashboard (legacy review UI)
mojo scan          Rule-based git / folder / Claude+Codex sessions scan (free)
mojo extract       Run the extraction pipeline (claude/codex CLI or API)
mojo sync          Explicit legacy write to CLAUDE.md / SKILL.md
mojo review        Approve / edit extracted items from the terminal
mojo search        Full-text search across the knowledge store
mojo stats         Show store statistics and extraction cost
mojo import-seed   Bulk-import a seed knowledge JSON file
```

### Typical workflows

```bash
# Bootstrap a new project from its git history (free, rule-based)
mojo attach --project ~/code/my-service
mojo scan git   ~/code/my-service
mojo scan folder ~/code/my-service
mojo scan notes ~/code/my-service

# Scan a public repo without adding it to your tree
git clone --depth 200 https://github.com/org/repo /tmp/repo
mojo scan git /tmp/repo

# Curated seed knowledge you already wrote
mojo import-seed seeds/seed_knowledge.json

# Manual entry via the dashboard — click "+ Add" in the top bar
mojo dashboard

# Backfill past Claude Code + Codex sessions for this project, then extract
mojo scan sessions --project ~/code/my-service        # --source claude|codex|all
mojo extract --project ~/code/my-service              # headless claude -p, $0

# Extract one Claude/Codex JSONL transcript directly (format auto-detected)
mojo extract --session /path/to/session.jsonl --session-id session-001

# Pick a backend per run
mojo extract --backend codex-cli
mojo extract --backend api --batch --parallel 4   # API: batches + parallel filter

# Mirror knowledge into the Obsidian vault / pull back human edits
mojo vault init      # first time: create vault + README
mojo vault sync      # import frontmatter edits, re-export notes

# Generate advisory context without editing human-authored instruction files
mojo refresh --project ~/code/my-service --task "debug list endpoint latency"

# Run the quiet companion intervention check once
mojo companion check --project ~/code/my-service --task "auto modify CLAUDE.md"

# Enable/disable lightweight local sidecar state
mojo companion start --project ~/code/my-service
mojo companion status --project ~/code/my-service
mojo companion stop --project ~/code/my-service

# Feed back on a shown intervention
mojo companion feedback ci-20260529123456000000 useful --accepted

# Optional explicit legacy injection into Claude-facing files
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
| **Git history**           | `mojo scan git /path/to/project`                    | T2 evidence |
| **Folder scan**           | `mojo scan folder /path/to/project`                 | T2 evidence |
| **Markdown notes/docs**   | `mojo scan notes /path/to/project`                  | T2 evidence |
| **Past Claude sessions**  | `mojo scan sessions`                                | T2 evidence until reviewed |
| **Live Claude sessions**  | automatic capture after hooks are installed         | T2 evidence until reviewed |
| **Hand-written seed**     | `mojo import-seed seeds/seed_knowledge.json`        | Candidate until reviewed |
| **Dashboard → + Add**     | click **+ Add** in the top bar of the dashboard     | Candidate until reviewed |
| **Remote GitHub repo**    | `git clone …` then `mojo scan git <dir>`            | T2 evidence |

Sources answer where the evidence came from. Tiers answer whether the
knowledge has been reviewed and is valuable enough to guide future work.

| Tier            | Meaning                                                                 |
|-----------------|-------------------------------------------------------------------------|
| **T1 Tacit**    | High-value scoped knowledge with approval or strong evidence. It has a clear `scope`, rationale, and applicability conditions. |
| **T2 Evidence** | Raw, cheap, candidate, or broadly available context. Useful for review and retrieval, but not a strong rule by itself. |

Manual input is not automatically T1. It becomes T1 only after review or
when it carries clear scope, rationale, and promotion state.

## The Obsidian Vault

`mojo vault sync` mirrors the knowledge store into a plain-markdown
vault (default `~/mojo-vault`, configurable via `vault.path`):

```
~/mojo-vault/
├── README.md            # review conventions
├── REVIEW-QUEUE.md      # auto-generated inbox: pending reviews + generalization candidates
└── knowledge/
    └── electricity/smp/
        └── smp-004 Use KST for SMP timestamps.md
```

One claim = one note. The frontmatter **is** the review UI — edit it in
Obsidian, then `mojo vault sync`:

- approve for this project → `promotion_state: project_approved`
- generalize → `promotion_state: generalized` + `scope: domain|universal`
- reject / archive → `promotion_state: rejected` / `archived: true`
- sharpen boundaries → `applies_when` / `does_not_apply_when`

Two-way sync is safe without timestamps because ownership is split:
human-editable fields (title, content, scope, promotion state, …) are
owned by the vault file; machine fields (observations, grades, related
links) are owned by the DB and regenerated on every export. Invalid enum
edits are rejected instead of corrupting the store. After auto-extraction
(see Hooks) new notes appear in the vault without any manual step.

## Claims and Observations

The core taxonomy question — *"is this knowledge project-specific or
general?"* — is deliberately **not answered at capture time**, because the
same claim can be true in one project and false in another.

- A knowledge row is a **claim**, captured project-scoped.
- Every sighting is an **observation**: `(project, session, supports|refutes)`.
- The TF-IDF dedup does not discard re-extractions — a re-observation from
  a *different* project is exactly the evidence generalization needs.
- Supporting observations from ≥ 2 distinct projects (with zero
  refutations) flag the claim as a generalization candidate in
  `REVIEW-QUEUE.md`. Promotion itself stays a human decision.
- A refuting observation never deletes the claim; it sharpens
  `does_not_apply_when` — the boundary is the knowledge.

## The Dashboard (legacy)

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
- **Scope and promotion review** — approve for a project, approve for a
  domain, generalize, keep as evidence only, reject, archive, or mark
  conflicts/counterexamples
- **Inline edit, approve, archive, delete, structure, refine** — all
  mutations persist to the same SQLite store `mojo refresh` reads from
- **Light / dark theme toggle** — persisted in `localStorage`
- **No build step** — React, ReactDOM, Babel-standalone, and d3 are
  loaded from CDN

## Companion Intervention Layer

The companion layer is a quiet observer over the same scoped knowledge DB.
It is not an autonomous agent and it does not edit code or instruction
files. It collects local context, retrieves relevant Mojo knowledge, then
chooses one of:

- `silent`
- `hard_warning`
- `soft_suggestion`
- `clarifying_question`

Default sensitivity is conservative. Raw evidence cannot trigger hard
warnings by itself. Candidate knowledge usually becomes a suggestion or a
clarifying question. Project-approved and generalized knowledge can trigger
warnings only when scope and context match.

The first implementation provides:

- project context collection: project path, git branch/diff, recent files,
  current task/event/command/output, and generated `MOJO.md`
- scoped retrieval through the advisory layer
- rule-based intervention classification
- terminal/JSON notification abstraction
- SQLite intervention logging and feedback
- sidecar status via `.mojo/companion.json`

`mojo companion check` is the safe one-shot mode. `mojo companion start`
starts a lightweight local sidecar process unless `--no-process` is used.
Both modes preserve `AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `SKILLS.md`, and
other human-authored instruction files. The companion may warn or suggest,
but actual edits require explicit user action outside the companion.

## Architecture

```
T1 · Tacit  ──┐
  seed import │
  manual entry│
              │
T2 · Evidence ┼──► SQLite store ──► Retriever ──► MOJO.md advisory context
  git scan    │   (dedup, grades, scope)             │
  folder scan │         ▲                            ▼
  sessions    ┘         └──── review / usage feedback ┘
```

The evidence-based grader and promotion state sit between the store and
retrieval, so raw or candidate items can be surfaced as evidence without
being treated as persistent instructions.

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
3. **Per-project attachment** — after a single global `mojo init`, run
   `mojo attach` inside any repo that should get its own advisory
   `MOJO.md` context. If you deliberately create a `.mojo/mojo.db`
   sidecar, the session hook
   walks up from cwd and routes transcripts to the nearest sidecar,
   falling back to the global store everywhere else. See
   [Per-project isolation](#per-project-isolation) for details.

## Cost

- Default (`claude-cli` backend): **$0** — runs on your Claude subscription.
- Signal detection and git scanning: **free** (rule-based, no LLM calls)
- `api` backend: ~$0.001/session (Haiku filter) + ~$0.005/candidate
  (Sonnet structuring); typical monthly total **$1–5** at 5–10 sessions/day.

All costs are tracked per stage in the `extraction_costs` SQLite table
(CLI backends record token usage with cost 0).

## LLM Backends

`mojo extract` runs on one of three backends (`extraction.backend` in
`~/.mojo/config.yaml`, or `--backend` per run):

| Backend          | Mechanism                | Cost            | Notes |
|------------------|--------------------------|-----------------|-------|
| **`claude-cli`** (default) | headless `claude -p` | **$0** (subscription) | filter=haiku, structure=sonnet. `ANTHROPIC_API_KEY` is stripped from the child env so billing can never silently fall through to a key — if you are not logged in, you get a loud error. |
| `codex-cli`      | headless `codex exec`    | **$0** (subscription) | uses your configured Codex model; no token usage reporting |
| `api`            | Anthropic API            | ~$0.04/session  | prompt caching, `--batch` (Message Batches, ~50% off structuring), exact per-stage cost tracking |

Rule-based features (git/folder scan, seed import, vault, advisory
generation) never call an LLM and work with no backend at all.

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

With the optimizations in the extraction pipeline, a typical session drops
substantially below the baseline above:

| Optimization                  | Mechanism                                          | Effect on session cost |
|-------------------------------|----------------------------------------------------|------------------------|
| **`try/finally` guard**       | Session is marked extracted even on crash          | Eliminates duplicate spend on retry |
| **Prompt caching**            | Static system prompt → `cache_control: ephemeral`  | ~10–30% ↓ (more on multi-candidate sessions) |
| **`--batch`**                 | Message Batches API for Sonnet structuring        | Additional **50% off** Sonnet stage |
| **`--parallel N`**            | Async Haiku filter across sessions                 | Throughput ↑, no cost change |

`--batch` is async — the batch may take minutes to hours to complete.
Prefer `--batch` for background/hook-triggered extraction and the default
synchronous path when you want immediate results.

## Requirements

- Python 3.10+
- `ANTHROPIC_API_KEY` (only for `mojo extract`; scanning and everything
  else run without it)
- Claude Code installed (so hooks can be registered; optional for the
  dashboard and git scanning)

## Roadmap

- [ ] End-to-end advisory pipeline: session hook → `extract` → `refresh`
      as a single visible trigger
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
