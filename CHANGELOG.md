# Changelog

All notable changes to this plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-09-06

Restructures the pipeline around an ephemeral per-stage sub-orchestrator, aligning
this plugin's architecture with its Gemini counterpart
([nsrandom/gemini-staged-build](https://github.com/nsrandom/gemini-staged-build)).

The previous design put every subagent's full output — implementation notes, test
transcripts, diffs, review findings, debug cycles — into the root orchestrator's
context. That works for one stage and collapses somewhere around stage six of a
`yolo` run. Stages are now delegated whole to an ephemeral agent that dies with its
transcript, and subagents write their evidence to disk instead of returning it.

### Added

- **`stage-runner`** — an ephemeral sub-orchestrator that runs one stage's entire
  lifecycle (implement ↔ verify → commit → report) in an isolated context and
  returns at most 1000 tokens. Verbose material never reaches the root
  orchestrator. In `yolo` it also specifies the stage; in `next` the root keeps
  that step so the user can approve the plan first, because subagents cannot
  prompt the user.

- **Compact return payloads.** `implementer` and `stage-architect` write exhaustive
  detail to `specs/<feature>/stages/*.report.md` and `*.detail.md`, and return a
  structured payload of ≤250 tokens. Raw terminal output, test-runner transcripts,
  and code diffs are prohibited in return messages.

- **`/staged-build:cleanup`** — reviews the decisions taken without asking, in a
  deliberately fresh context loaded only from disk. Tier 1 decisions are confirmed
  in a single batch; Tier 2 and flagged ones are walked one at a time with confirm
  / reject / defer / follow-up / modify. Anything rejected or modified, plus the
  scratchpad, becomes a real cleanup stage that runs through the normal pipeline.

- **`/staged-build:analyze-tokens`** and `scripts/analyze_tokens.py` — reads Claude
  Code's own transcripts under `~/.claude/projects/` and reports tokens, wall-clock
  time, and tool calls per role, per stage, and per invocation, writing
  `tokens_efficiency_report.md` and `.json`. It audits the pipeline's invariants:
  verifier independence, the ≤250-token return budgets, fix-cycle counts, and
  runaway agents. Both orchestrators suggest it when a stage exceeds ~15 minutes or
  2 fix cycles, and when the feature completes.

- **`SESSION_STATE.json`** — the test command, interpreter, and other environment
  facts, recorded once by the plan-architect and read by every later stage. Ends
  the environment amnesia that made each stage rediscover how to run the tests.

- **`specs/<feature>/scratchpad/`** — a gitignored home for one-off verification
  code: probe scripts, scratch databases, harnesses that reach into internals.
  Long-term tests go to the project's main tests directory instead; the verifier
  checks the separation held, and `cleanup` deletes the scratchpad.

- **Inter-stage context bridging.** The stage-architect for stage N > 1 receives
  the Summary and Changes sections of every prior stage report as ground truth for
  what actually landed, which stops a late stage being specified against an
  interface an earlier one never built.

- **Tiered decisions.** Minor decisions are classified Tier 1 (routine convention)
  or Tier 2 (shapes behaviour or a contract), which is what lets `cleanup` confirm
  fifteen naming choices in one click and still give real attention to the four
  that matter.

### Changed

- **Seven agents became five.** `reviewer` and `validator` merged into
  **`verifier`**, which both reads the diff against the contract and runs the
  software — the split forced two full context loads to answer one question. The
  `debugger` was folded into `implementer`, which now self-heals from verifier
  findings; a separate agent re-derived the whole stage from scratch to fix its own
  code. `plan-checker` was removed: an LLM checking a plan it cannot run found
  mostly what the implementer finds anyway, at the cost of an opus run per stage.
  In `next` its gate is replaced by **you** approving the stage plan, which is the
  check that was actually worth having.

- **Planning writes to disk on the first pass.** `plan-architect` now saves
  `SPEC.md`, `STATE.md`, `DECISIONS.md`, and `SESSION_STATE.json` immediately and
  edits them in place through the conversation, so you review a real file in your
  editor rather than a proposal in chat.

- **`DECISIONS.md` is used in `next` too**, not only `yolo`. A minor decision no
  longer stops a supervised run; it is logged and reviewed at `cleanup`.

- **Decision review moved out of `yolo`** into `/staged-build:cleanup`, so it
  starts from a lean context instead of inheriting the whole run's transcript.

- **`pipeline.json` roles** are now `plan-architect`, `stage-runner`,
  `stage-architect`, `implementer`, `verifier`. A project `.claude/pipeline.json`
  naming the old roles will not route the new ones — update it.

- **The verifier's payload is the contract *and* the stage diff.** The validator
  previously received the contract alone; the verifier needs the diff to do the
  reading half of its job. Everything else remains excluded: no `.detail.md`, no
  implementer report, no decision log, no autonomy addendum.

### Removed

- The `plan-checker`, `reviewer`, `validator`, and `debugger` agents, and the
  plan-check step and its revise→check cycle.

### Migration

Bump past a cached install (`claude plugin uninstall staged-build &&
claude plugin install staged-build@staged-build`), and update any project
`.claude/pipeline.json` to the five new role names. Features planned under 0.6.0
keep working: `next` creates the missing `DECISIONS.md` and `SESSION_STATE.json`
when it reaches a stage.

## [0.6.0] — 2026-09-02

Branch and commit conventions for feature work.

### Changed

- **Feature branches are named `<feature>`, not `staged-build/<feature>`.** The
  prefix carried no information the `Branch:` line in `STATE.md` didn't already
  hold, and it made branches awkward to type and to read in `git branch`.
  Both orchestrators, and the plan-architect's `STATE.md` template, now say so.

- **A branch that already exists is now a question, not an assumption.** `next` and
  `yolo` used to check out an existing `<feature>` branch silently. Reusing a branch
  someone else started is not cheaply reversible, so both now stop and ask, showing
  what is already on it. Under `yolo` this is a major decision, which stops the run.

- **Commits on a feature branch are prefixed `<feature>-stage-NN: `** rather than
  `stage NN: `. Across a repo with several features in flight, `stage 03:` says
  nothing about which feature it belongs to. This applies to the message `yolo`
  commits, the per-`[large]`-step message it hands the implementer, the `jj`
  change description, and the message `next` describes for the user to commit.

- **Neither orchestrator will build a stage on `main`/`master`.** This was implied
  before by the branch-resolution rules; it is now stated as its own constraint.

## [0.5.0] — 2026-09-02

Rebalances the pipeline away from planning and towards building.

A measured `/staged-build:yolo` run on a three-stage feature completed **one** stage
and spent ~2.5M subagent tokens and ~150 minutes of wall time doing it:

| Role | Calls | Tokens | Share | Wall |
|---|---|---|---|---|
| `plan-checker` | 13 | 1,249,461 | 49.4% | 94.5 min |
| `stage-architect` | 7 | 1,122,611 | 44.3% | 43.8 min |
| `implementer` | 1 | 78,529 | 3.1% | 4.3 min |
| `reviewer` | 1 | 46,867 | 1.9% | 2.6 min |
| `validator` | 1 | 34,313 | 1.4% | 1.5 min |
| `debugger` | 0 | 0 | 0% | — |

93.7% of the tokens went to planning and to checking plans; 3.1% went to writing
code. Two causes. The detail file had become a second, unexecuted implementation —
for one stage, 1,723 lines containing the verbatim text of a 170-line script and a
~280-line test module — and three plan-checkers then reasoned about what that
unexecuted code would do, at ~330k tokens a round. And sorting the run's findings by
"would a post-implementation gate have caught this?" split them in half: a regex
matching 212 files instead of 3, a wrong pipeline count, a step-ordering bug and a
wrong interpreter path were all free from any test run, while a documented config
idiom that silently didn't work, a reversible password digest, five assertions that
printed plaintext secrets on failure, and an irreversible action needing a user
decision were not. Only the second group justifies checking a plan before it is
built — and three of those four were really *code* review, performed on code that
happened to be sitting in a spec file.

### Changed

- **The stage-architect's detail file is capped.** It must not exceed roughly 300
  lines and must never contain a complete implementation of any file: signatures,
  data shapes, edge cases, and test *names*, not the test module or the script
  written out. A spec longer than the diff it describes is now stated to be a
  defect. Steps are still marked `[large]` — `yolo` uses the mark for per-step
  commits — but a `[large]` step no longer gets a stand-alone subsection deep enough
  to be checked on its own, because nothing checks steps any more.
- **The plan-checker has one mode and a much narrower brief.** Step-plan mode is
  gone. It no longer verifies every citation or builds a criteria coverage map, and
  it is told to assume the implementer is competent and the test suite is real.
  It reports three things: behaviour the contract specifies that the plan gets wrong
  or cannot deliver; properties a passing test suite would not detect — secrets and
  personal data on any path, weak or reversible handling, silent-failure modes,
  irreversibility; and decisions the user must take before an irreversible action,
  which go in **Questions**. The PASS/FAIL contract is unchanged.
- **The plan-checker may now execute anything that does not modify the repository
  tree** — run a snippet from the plan, measure what a glob really matches,
  reproduce a failure in `/tmp` — where it was previously restricted to reading.
  In the measured run its best findings all came from doing exactly this, against
  its own stated rules. It still has no Write and no Edit and must leave the tree
  as it found it.
- **One plan check per stage, and one revision.** The per-`[large]`-step
  invocations are removed from both `stage` and `yolo` (3 checks per stage → 1), and
  the revise→check limit drops from 2 cycles to 1: a second failure stops and
  reports every finding from both attempts.
- **The reviewer takes on the failure-path work** the plan-checker used to attempt
  from a distance. It now traces each error branch to what it actually emits — what
  gets printed, logged, or persisted when an assertion fails or an error propagates
  — and asks whether anything reaches a terminal, a log, or a committed file that
  should not: secrets, credentials, tokens, personal data. This is the review that
  catches the defects a green test suite hides.
- **The implementer is told the spec is a spec.** It no longer looks for a **Large
  steps** subsection that the stage-architect has stopped writing; `[large]` now
  reads to it as a size and commit-boundary signal. It is told explicitly that the
  detail file gives signatures, data shapes, edge cases and test names rather than
  function bodies, that this is deliberate and not an omission to stop over, and
  that it decides the local calls a spec should not be making for it. Its account of
  the plan check is corrected too: the check is narrow and does not confirm every
  signature and path the plan quotes, so confirming those is the implementer's job.
  Nothing about its scope rules, its stop conditions, or its report changed.
- README updated to match: the `next` step list, the plan-checker's permissions and
  cost, and the note about what the `[large]` mark now buys.

### Unchanged

- `validator` and `debugger` — along with the implementer, the best value per token
  in the measured run. Untouched. The implementer's own edit is confined to what the
  spec now contains; its behaviour is unchanged.
- The pipeline shape, the `STATE.md` / `DECISIONS.md` layout, the `yolo` autonomy
  policy, and the branch and commit policy. No new agents.

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
