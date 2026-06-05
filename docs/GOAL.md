# Goal: Reframe Mojo as an attach/detach tacit-knowledge reference layer

## Context

This project is not just a generic memory extractor. The core value of Mojo should be turning project work, Codex/Claude sessions, commit logs, JSONL traces, and user decisions into structured tacit knowledge.

However, the current design risks over-generalizing cheap or local observations into global rules. This is dangerous because some knowledge is universally useful, while other knowledge is only valid for a specific project, environment, incident, workflow, or user preference.

The goal is to redesign and implement the next step so Mojo becomes a local-first tacit-knowledge system that can be attached to or detached from Codex/Claude-style agents without automatically mutating human-authored instruction files.

## Product direction

Mojo should not directly overwrite or silently modify existing human-authored instruction files such as:

* AGENTS.md
* CLAUDE.md
* SKILLS.md
* project-specific manual instruction files

Those files should remain explicitly human-controlled.

Instead, Mojo should provide a separate reference layer, such as `MOJO.md` plus a local tacit-knowledge database. During reasoning or task execution, an agent can retrieve relevant knowledge from Mojo and use it as advisory context. Actual injection into AGENTS.md, CLAUDE.md, or SKILLS.md must remain manual or explicitly approved.

In other words:

* Mojo collects and structures tacit knowledge.
* Mojo retrieves and maps relevant knowledge to the current task.
* Mojo recommends or surfaces knowledge.
* Mojo does not silently promote every extracted rule into global instructions.
* Human approval is required before strong or persistent instruction injection.

## Required conceptual model

Add or refactor the knowledge model so that “quality” and “applicability scope” are separate concepts.

Do not treat confidence, grade, tier, and scope as the same thing.

Introduce fields or equivalent structures for:

* `taxon`: the kind of knowledge
* `scope`: where the knowledge is valid
* `applies_when`: conditions where the knowledge is likely useful
* `does_not_apply_when`: conditions or counterexamples
* `evidence_level`: whether this is raw observation, local rule, generalized principle, or policy-like guidance
* `promotion_state`: raw, candidate, project-approved, generalized, rejected, archived
* `project_id` or `project_path` where relevant
* `source_lineage`: session, jsonl, commit, manual note, review action, etc.
* `conflicts_with` or `counterexamples` where applicable

Suggested `scope` values:

* universal
* domain
* project
* environment
* workflow
* incident
* user_preference

Suggested `taxon` values:

* implementation_pattern
* debugging_pattern
* evaluation_rule
* leakage_warning
* environment_constraint
* project_convention
* user_preference
* decision_rationale
* anti_pattern
* tool_usage_rule
* deployment_note
* research_hypothesis
* unresolved_question

These values can be adjusted if the existing codebase has a cleaner taxonomy, but the final design must clearly distinguish what kind of knowledge it is and where it applies.

## Tier semantics

Revise tier semantics so they reflect value and validation, not just source type.

Use this interpretation:

* Tier 2 knowledge is cheap, raw, or broadly available information. It can come from commit logs, traces, repeated observations, or generic project context. It is useful evidence but should not automatically become a strong rule.
* Tier 1 knowledge is high-value tacit knowledge. It usually comes from user judgment, repeated validated experience, hard-won debugging, project-specific conventions, or decisions made while working with LLM agents. Tier 1 must have explicit scope and approval or strong evidence.

Avoid promoting a knowledge item to Tier 1 merely because it was manually entered. Manual input can be a signal, but Tier 1 should still require clear applicability, rationale, and review status.

## Extraction behavior

Change extraction prompts and logic so the system does not aggressively produce “Always do X” or “Never do Y” rules unless the evidence really supports that strength.

The extractor should prefer this structure:

1. Observation
2. Local context
3. Possible rule
4. Applicability conditions
5. Non-applicability conditions
6. Evidence source
7. Suggested promotion state
8. Whether human review is required

The extractor should explicitly ask whether a rule is:

* only true for this repository
* only true for this stack
* only true for this environment
* only true for this incident
* likely generalizable
* too weak and should remain as evidence only

## Retrieval and advisory use

Implement or refactor retrieval so that Mojo can map the current task to relevant tacit knowledge.

The retrieval should consider:

* project path
* current repository
* file paths being edited
* task type
* tool being used
* detected domain
* current error messages
* related commits or traces
* explicit user goal

The output should be advisory, not absolute. It can generate a `MOJO.md` or equivalent task-context file containing:

* relevant knowledge items
* why each item was selected
* scope and applicability warnings
* conflicts or counterexamples
* source lineage
* whether the item is approved, candidate, or raw

Do not blindly inject raw or candidate knowledge as system-level instructions.

## Attach/detach workflow

Add an attach/detach style workflow so users can easily apply Mojo to a project or remove it.

Desired behavior:

* `mojo attach` initializes project-local Mojo metadata/config without damaging existing files.
* `mojo detach` removes or disables Mojo project integration while preserving the central knowledge database unless explicitly requested.
* `mojo status` shows whether the current project is attached, what knowledge DB is used, and what files/hooks/configs are active.
* `mojo refresh` or equivalent regenerates advisory context such as `MOJO.md`.
* If hooks are used, they must be clearly visible and reversible.

The design should be local-first and easy to distribute. Avoid requiring a complex server setup for basic usage.

## Backfill and future accumulation

Support both historical and future knowledge accumulation.

Historical backfill sources:

* existing JSONL sessions
* Claude/Codex logs
* commit history
* markdown notes
* existing project docs
* manual seed files

Future accumulation sources:

* new Codex/Claude sessions
* new commits
* review actions
* accepted/rejected knowledge candidates
* explicit user corrections
* project-specific notes

The system should preserve raw evidence and then create structured candidates. Do not destroy raw traces during summarization.

## Dashboard/review behavior

If a dashboard or review UI exists, update it to expose the new conceptual model.

The reviewer should be able to see and edit:

* taxon
* scope
* applies_when
* does_not_apply_when
* evidence level
* promotion state
* source lineage
* counterexamples
* conflicts
* whether it is safe to generalize

Review actions should include:

* approve for this project only
* approve for this domain
* approve as general principle
* keep as evidence only
* reject
* archive
* mark as conflicting with another item

## Safety and correctness constraints

Do not auto-modify AGENTS.md, CLAUDE.md, or SKILLS.md unless the user explicitly requests it.

Do not delete existing knowledge or migrations without a compatibility path.

Add migrations if the schema changes.

Preserve local-first behavior.

Add tests for:

* schema migration
* extraction of scoped knowledge
* prevention of over-generalized Always/Never rules
* attach/detach behavior
* advisory MOJO.md generation
* retrieval by project/path/task
* promotion-state transitions
* raw evidence preservation

Also fix obvious bugs discovered during inspection if they block the new workflow, such as undefined variables or broken extraction paths.

## Deliverables

Implement the smallest coherent version of this design.

Expected deliverables:

1. Updated data model or migrations.
2. Updated extraction prompts and parsing.
3. Updated retrieval/packing logic to respect scope and promotion state.
4. Attach/detach/status workflow or equivalent CLI.
5. Advisory `MOJO.md` generation that does not overwrite human instruction files.
6. Review/dashboard updates if the dashboard already exists.
7. Tests for the new behavior.
8. Documentation explaining the new mental model: Mojo as a tacit-knowledge reference layer, not an automatic global instruction writer.

## Validation

After implementation, run the relevant test suite and type/lint checks available in the repository.

At minimum, inspect the repo and run the existing project-appropriate checks. If a full check is unavailable or too expensive, document what was run and what remains unverified.

Do not weaken tests to pass.

Do not remove existing functionality unless necessary.

Prefer small, well-scoped commits.
