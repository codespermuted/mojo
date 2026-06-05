# GOAL-2: Mojo Companion Intervention Layer

## Context

The previous goal is already running and focuses on reframing Mojo as an attach/detach tacit-knowledge reference layer.

This second goal should be implemented after or alongside that work without disrupting the first goal. Treat this as a follow-up extension, not a replacement.

The purpose of this goal is to add a lightweight companion system that observes the user’s current work context, retrieves relevant tacit knowledge from Mojo, and warns or advises the user only when there is a high-value reason to intervene.

The companion should eventually be representable as a small desktop character with a speech bubble, but the first implementation should prioritize the intervention logic, local sidecar architecture, and notification abstraction. Do not start by overbuilding the character UI.

## Product concept

Mojo should support a local companion mode.

When enabled, Mojo runs as a local sidecar process that is independent of any single CLI. It should be able to observe project context, retrieve relevant knowledge from the Mojo database, and decide whether to remain silent, suggest a better direction, ask a clarifying question, or warn the user.

The companion should be useful while the user works with tools such as Codex, Claude Code, Claude, terminal workflows, IDEs, and project files.

It should not require every workflow to be launched through a Mojo wrapper. A wrapper can be added later, but the primary architecture should be sidecar-first and integration-friendly.

## Core principle

The companion must be quiet by default.

It should not constantly comment on normal work. It should only intervene when Mojo has a strong reason to believe that:

1. The current direction repeats a previously failed or dangerous pattern.
2. A clearly better known approach exists based on scoped tacit knowledge.
3. The current prompt, implementation path, or macro-level direction conflicts with an approved project rule.
4. A critical decision boundary is missing.
5. The user is about to violate an explicit safety, leakage, data, or instruction-management constraint.

The companion should not act like an autonomous boss. It should behave like a small advisory observer that speaks only when the expected value of speaking is high.

## Intervention types

Implement an intervention model with at least the following categories:

* `silent`
* `hard_warning`
* `soft_suggestion`
* `clarifying_question`

Default behavior must be `silent`.

### hard_warning

Use this when the current direction appears clearly risky, invalid, or previously known to fail.

Examples:

* The user or agent is about to auto-modify `AGENTS.md`, `CLAUDE.md`, or `SKILLS.md` without explicit approval.
* A project-specific rule is being generalized into a global instruction.
* A time-series experiment appears to violate a known leakage constraint.
* A command risks modifying production-like data.
* The current implementation path matches a previously failed pattern in this repository.
* The agent is ignoring an approved project-level constraint from Mojo.

Hard warnings should require strong evidence, high confidence, or explicit approved rules.

### soft_suggestion

Use this when Mojo has a likely better approach, but the current direction is not clearly wrong.

Examples:

* A similar task previously succeeded with another implementation order.
* A known debugging pattern applies to the current error.
* A project convention exists and may reduce rework.
* Prior work suggests adding tests or schema migration before implementation.
* A known prompt structure is more suitable for this kind of task.

Soft suggestions should be phrased as optional advice with evidence.

### clarifying_question

Use this when an important distinction is missing.

Examples:

* It is unclear whether a rule should be project-specific, domain-level, or universal.
* A knowledge item has conflicts or counterexamples.
* A change may require human approval before being promoted.
* The current goal is ambiguous enough that automation could go in a harmful direction.

### silent

Use this for normal work, weak matches, raw evidence, low-confidence retrievals, and situations where the user is already following a reasonable path.

## Context sources

Design the companion to consume context from multiple sources.

Initial implementation can be minimal, but the architecture should allow future expansion.

Possible context sources:

* current project path
* git branch
* git diff
* recently modified files
* current file paths
* terminal command history if available
* recent command output
* error logs
* Codex or Claude JSONL session files
* current user goal
* generated `MOJO.md`
* Mojo knowledge DB
* review decisions
* approved/rejected knowledge history
* promotion state history

For the first coherent version, implement a practical subset such as:

* project path
* git diff
* recently modified files
* Mojo DB retrieval
* generated advisory context
* simple local log/event input

Do not block the whole feature on full Codex/Claude integration.

## Intervention decision engine

Implement a decision engine that receives current context and retrieved Mojo knowledge, then emits one of the intervention types.

Inputs should include:

* current project context
* matched knowledge items
* scope match
* promotion state
* evidence level
* source lineage
* confidence
* risk level
* similarity to previous failure or success patterns
* whether the knowledge is approved, candidate, or raw

Output should include:

* intervention type
* short message
* evidence summary
* recommended action
* source knowledge IDs
* confidence
* whether user feedback is requested

The output must explain why Mojo is speaking.

Bad:

> This is wrong. Stop.

Good:

> Warning: this resembles a previous leakage failure in this project. The matched knowledge item says forecast features must respect issue-time availability. Recommended action: verify the feature manifest before continuing.

## Notification abstraction

Separate intervention logic from UI.

The first version can notify through one or more simple channels:

* terminal advisory output
* local dashboard event
* OS notification
* local HTTP endpoint
* small floating window

The final visual concept can be a small character with a speech bubble, but this should be built as a presentation layer over the notification abstraction.

Do not tightly couple the companion to a specific UI framework in the first version.

## Companion commands

Add or design CLI commands such as:

* `mojo companion start`
* `mojo companion stop`
* `mojo companion status`
* `mojo companion check`
* `mojo companion feedback`

Suggested behavior:

### `mojo companion start`

Starts the local sidecar watcher/intervention process for the current project.

### `mojo companion status`

Shows whether companion mode is active, which project is attached, which knowledge DB is used, and which notification channels are enabled.

### `mojo companion check`

Runs a one-shot intervention check against the current project context without starting a long-running process.

### `mojo companion feedback`

Allows the user to mark a shown intervention as useful, not useful, too noisy, too weak, or wrong.

Exact command names can be adjusted to match the existing CLI style.

## Feedback loop

Every intervention should be logged.

Track at least:

* timestamp
* project path
* context hash
* retrieved knowledge IDs
* intervention type
* message
* recommended action
* user feedback
* whether the suggestion was accepted
* whether it was dismissed
* whether it was marked noisy or wrong

Feedback should be usable later to improve intervention thresholds.

The system should learn that some warnings are too noisy and should be weakened, while some useful warnings should become stronger candidates for project-approved knowledge.

## Knowledge safety rules

The companion must respect the scoped-knowledge model from the previous goal.

Rules:

* Raw evidence should not trigger hard warnings by itself.
* Candidate knowledge should usually trigger only soft suggestions or clarifying questions.
* Project-approved knowledge can trigger hard warnings inside the matching project scope.
* Generalized knowledge can trigger broader warnings, but only if its `scope`, `applies_when`, and `does_not_apply_when` are compatible with the current context.
* Conflicting knowledge should lower confidence or trigger a clarifying question.
* The companion must surface source lineage and scope when warning the user.

The companion must not silently modify:

* `AGENTS.md`
* `CLAUDE.md`
* `SKILLS.md`
* human-authored project instruction files

It can suggest modifications, but actual changes must require explicit user approval.

## Example messages

Example hard warning:

> Warning: this direction matches a previously rejected pattern in this repository. The matched Mojo knowledge item says not to auto-promote project-specific observations into global instructions. Recommended action: keep this as project-scoped advisory knowledge first.

Example soft suggestion:

> Suggestion: a similar migration task previously succeeded when schema migration and tests were added before dashboard changes. Consider implementing the migration and test path first.

Example clarifying question:

> Clarification needed: this knowledge item may be valid only for this project. Should it remain project-scoped, or do you want to review it for domain-level promotion?

Example leakage warning:

> Warning: this experiment may violate a known forecast-vintage constraint. Check whether every feature is available at the prediction issue time before training.

## UX requirements

The companion should be non-disruptive.

The user should be able to:

* enable or disable companion mode
* snooze notifications
* configure sensitivity
* inspect supporting evidence
* mark interventions as useful or not useful
* reject noisy interventions
* promote or demote the underlying knowledge item
* open the relevant Mojo knowledge record

Default sensitivity should be conservative.

## Implementation constraints

Implement the smallest coherent version.

Do not build a complex autonomous agent.

Do not require a remote server.

Preserve local-first behavior.

Do not break the existing attach/detach workflow.

Do not mutate human-authored instruction files.

Do not weaken existing tests.

Prefer simple, testable components:

* context collector
* knowledge retriever
* intervention classifier
* notification emitter
* intervention log
* feedback handler

## Deliverables

Expected deliverables:

1. Companion/intervention data model.
2. Local sidecar or one-shot watcher/check mode.
3. Retrieval of relevant Mojo knowledge for the current project context.
4. Rule-based intervention decision engine.
5. Minimal notification output.
6. Intervention logging.
7. Feedback capture.
8. Documentation for companion mode.
9. Tests for:

   * silence-by-default behavior
   * hard-warning thresholds
   * soft-suggestion classification
   * clarifying-question classification
   * scope-aware intervention
   * prevention of hard warnings from raw evidence alone
   * feedback logging
   * no mutation of `AGENTS.md`, `CLAUDE.md`, or `SKILLS.md`

## Validation

Run the existing test suite or the repository-appropriate validation commands.

Also add targeted tests for the new companion logic.

If full validation is not possible, clearly document:

* what was run
* what passed
* what remains unverified
* any known limitations

## Non-goals for this iteration

Do not prioritize:

* a polished animated character UI
* deep IDE extension integration
* full Codex/Claude wrapper integration
* autonomous code modification
* automatic instruction-file rewriting
* remote sync
* multi-user enterprise deployment

These can come later.

For this goal, prioritize the intervention engine, context awareness, local sidecar shape, and safe notification path.
