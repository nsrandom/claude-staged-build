# Changelog

All notable changes to this plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-09-01

### Added

- **`/staged-build:yolo`** — a second command that runs every pending stage of a
  plan back to back instead of one per invocation. Same per-stage pipeline
  (stage-architect → plan-checker → implementer → reviewer → validator, same cycle
  limits), with three additions:

  - **An autonomy policy.** Every judgement call a subagent hands back is triaged
    as *major* — stop and ask the user — or *minor* — decide, log, continue. Major
    covers anything that changes scope or acceptance criteria, anything not cheaply
    reversible (data model, schema, migration, on-disk or wire format, public API,
    new dependency), anything touching auth, credentials, billing, or user data,
    anything destructive or outward-facing, any convention the user has not set,
    and every pipeline stop condition (implementer stopped, stage split, conflict,
    `REPLANNED`, a check still failing after its 2 cycles). A call that is still
    ambiguous after a moment's thought is major.
  - **A decision log.** `specs/<feature>/DECISIONS.md`, written only by the
    orchestrator from the structured **Autonomous decisions** section each subagent
    now appends to its report. Every minor decision must land as one named place to
    change — a constant, a default parameter, a config key — and the entry records
    that `file:line`. Building an abstraction to hold a small decision is
    explicitly ruled out.
  - **A closing review stage.** When the last planned stage passes, `yolo` appends
    a `Confirm autonomous decisions` row to STATE.md and walks the user through
    every `unconfirmed` entry. Unpicked decisions become `confirmed`; requested
    changes go to the plan-architect as appended stages and the run continues
    through them.

- `yolo` commits, where `next` only describes the commit: one commit per stage, and
  one per `[large]` step within a stage. It records the stage's base commit before
  the implementer runs and hands the reviewer `git diff <base>` rather than
  `git diff`, so mid-stage commits do not hide half the diff from review.
- `yolo` takes an optional goal — `/staged-build:yolo "<goal>"` — and runs the
  normal planning conversation first when no plan exists. Plan approval stays an
  explicit user gate.
- `yolo` branch resolution adopts the current branch when it is already a clean new
  branch (not the default branch, nothing dirty outside `specs/`, no commits of its
  own) and creates `staged-build/<feature>` otherwise, recording either on the
  `Branch:` line. It never creates a second branch, deletes one, merges, rebases,
  or pushes.

### Notes

- The reviewer, validator, and plan-checker never receive the decision log or the
  autonomy addendum. The validator still gets the stage file and nothing else — an
  unattended run makes that independence the last honest check in the pipeline
  rather than a formality to trim.
- `/staged-build:stage` is unchanged. `next` still runs exactly one stage, asks
  before deciding anything, and leaves the commit to you.

## [0.3.0] — 2026-09-01

### Added

- **`plan-checker`** — a seventh role (opus), read-only, that verifies a stage's
  implementation plan against the real codebase *before* anything is implemented.
  It checks that every signature, path, and command the plan quotes actually exists
  as quoted, that every acceptance criterion maps to a step, that the verification
  command would fail today, and that no step is ambiguous enough to stall the
  implementer. Emits `VERDICT: PASS` / `VERDICT: FAIL`; a Critical or Major finding
  fails the plan.
- `pipeline.json` gains a `plan-checker` key.
- The stage-architect marks steps `[large]` (roughly 150+ lines of diff, or a step
  that introduces an interface or carries its own design decisions) and writes each
  one a **Large steps** subsection at whole-stage depth. Every `[large]` step gets
  its own plan-checker invocation — one per step, never batched.
- The stage-architect gains a **revision pass**: re-invoked with plan-checker
  findings verbatim, it fixes the plan files and reports what it changed and what it
  rejected.
- `STATE.md` gains a `Branch:` line, written by the plan-architect as
  `(not created yet)` and filled in by the orchestrator when the first stage starts.

### Changed

- **Breaking (workflow):** `next` creates **one branch per feature**, not per stage.
  The first stage creates `staged-build/<feature>` and records it in `STATE.md`;
  every later stage checks out the same branch and commits onto it. The orchestrator
  creates a second branch only when the user explicitly asks — `next --new-branch`
  or in prose — and records that branch as the new one for all following stages.
  Existing plans keep working: a `STATE.md` with no `Branch:` line is treated as
  "first stage" and has the line added.
- `next` will not implement an unchecked plan. Steps 5–7 (stage plan check, large
  step checks, revise→check on failure, max 2 cycles) sit between the
  stage-architect and the implementer.
- `redo` no longer deletes the stage branch. It stays on the feature branch and
  resets the current stage's work off it, naming the exact ref before it asks. The
  orchestrator never deletes a branch on its own.
- `status` reports the feature's branch, and flags it if the branch is missing or is
  not the one checked out.
- The validator's isolation now explicitly excludes plan-check verdicts along with
  the implementer's report and the reviewer's verdict.
- Stage reports record the plan-check findings that were fixed before
  implementation.
- Default routing for `reviewer` and `debugger` moved from opus to sonnet.

## [0.2.0] — 2026-08-30

### Changed

- **Breaking:** the `architect` role is split into two, and the `architect` key in
  `pipeline.json` is replaced by `plan-architect` and `stage-architect` (both
  default to opus). A config still using `architect` leaves both roles on their
  frontmatter default.
- `plan` is now a conversation. The plan-architect proposes stages and questions
  without writing anything; the orchestrator puts them to the user, relays the
  answers and any add/edit/delete/reorder, and re-proposes until the user
  approves. Only then are `SPEC.md` and `STATE.md` written.
- The plan-architect analyses the whole goal for feasibility. Genuine unknowns
  become stage 01 — an investigation stage — so no stage builds on an unverified
  assumption.
- `SPEC.md` gains a **Stages** section: every stage described in a paragraph.
- Stage specs are no longer written during planning. The stage-architect writes
  `NN-slug.md` (acceptance contract) and `NN-slug.detail.md` (implementation plan)
  when the stage begins, so each is informed by the code that earlier stages
  actually produced rather than by what the plan predicted.
- The orchestrator gained `AskUserQuestion`, needed to relay the plan-architect's
  questions. Subagents cannot prompt the user — Claude Code removes that tool from
  every subagent — so the orchestrator is their only channel.

## [0.1.0] — 2026-08-29

Initial release.

### Added

- Five role agents: `architect` (opus), `implementer` (sonnet), `reviewer` (opus),
  `validator` (sonnet), `debugger` (opus).
- `/staged-build:stage` orchestrator with `plan`, `next`, `status`, and `redo`.
- `pipeline.json` model routing, overridable per project via
  `.claude/pipeline.json`, with agent frontmatter as a last-resort default.
- Marketplace manifest pointing at `./`, so the plugin installs from a local
  clone during development and from a Git remote later.
- `docs/gemini-optional.md` — design note for routing the validator to a
  non-Claude model. Documented only; not implemented.

### Notes

- Plan state lives in `specs/<feature>/`, not `.claude/plan/`. Claude Code treats
  `.claude/` as a sensitive path and refuses writes there even with an explicit
  allow rule, which hard-fails in non-interactive runs.
- The validator is deliberately given only the stage file. It never receives the
  implementer's report or the reviewer's verdict.
- The `next` pipeline runs exactly one stage and stops. It never auto-continues.
