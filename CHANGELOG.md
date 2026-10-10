# Changelog

All notable changes to this plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] — 2026-10-10

The root orchestrator stays lean in both `next` and `yolo`: subagents load their own
context from disk, and the root stops reading files in order to paste them. What
each role receives is unchanged from 1.3.0; only how it is delivered changed. And
`next` now reminds you to `/clear` when earlier work is still sitting in the
conversation.

### Added

- **`scripts/handoff.py`.** `--feature <slug> --stage <NN> --role
  <stage-architect|stage-runner|approval>` prints that role's bundle with a
  `=== path ===` header per part, and exits non-zero naming any missing required
  file. `stage-architect` is `SPEC.md`, `STATE.md`, `SESSION_STATE.json`, the
  feature's `architecture.md`, and the previous stage's full report — the last two
  optional for the first stage, where a one-line note stands in. `stage-runner`
  adds `NN-slug.md` and `NN-slug.detail.md`. `approval` is only what `next` shows
  before running a stage: acceptance criteria, verification command, files
  expected to change, the test plan, `[large]` step titles, and the architect's
  `## Autonomous decisions`. `--include <path>` (repeatable) adds another
  feature's `architecture.md` or a findings file; `--check` validates without
  printing the bundle. Standard library only.
- **A clear-context reminder in `next`.** `scripts/context_check.py` reads the
  current session's transcript (by `CLAUDE_CODE_SESSION_ID`, falling back to the
  newest transcript for the repo) and reports how much the context has grown since
  the session's first turn. `next`'s preamble runs it, and when earlier work in the
  session has added 25k tokens or more, `next` says so before starting and asks
  whether to stop for a `/clear` or continue. It asks once per stage and never
  blocks: a missing transcript reads as `unknown` and is ignored.

### Changed

- **Delegation by path.** In `next` and `yolo`, the stage-architect and
  stage-runner prompts carry the mode, feature, stage, routing config, the
  `handoff.py` command to run first, and any `--include` paths — not pasted file
  text. Only what exists solely in the conversation stays inline and verbatim: the
  user's answers and plan feedback, and relayed findings. `next`'s revision pass
  goes the same way, with the feedback quoted. The root runs `--check` before every
  delegation. Routing models and foreground runs are unchanged.
- **The stage-architect and stage-runner accept either form.** Given a handoff
  command they run it first and read nothing else under `specs/` beyond their
  role's own needs; a missing required file is a `CONFLICT` or `HALTED`, never a
  guess. Inline text still works. The stage-architect may add an integrated
  feature's `architecture.md` it finds it needs, and reports it under a new
  `INCLUDES` payload field.
- **The implementer gets `architecture.md` by path**, alongside the contract and
  detail the runner already passes as text. The verifier still gets only the
  contract and the diff; relays and WIP checkpoints are unchanged.
- **Bookkeeping moved off the root.** On `VERDICT: PASS` the stage-runner appends
  the architect's `## Autonomous decisions` to `DECISIONS.md`, numbered on from the
  highest ID, before it rewrites `architecture.md`. It also logs any answer the
  `yolo` root gave, as `Decided by: orchestrator`. The stage-architect's
  `DECISION_IDS` now carry each tier and every Tier 2 title, so the root decides
  from payloads alone. Budgets are unchanged (architect 350, runner 800).
- **What the root reads** is now `STATE.md` (edited in place), the payloads, the
  `approval` bundle in `next`, and a specific section on demand. `SPEC.md`,
  `architecture.md`, stage files, report bodies, `DECISIONS.md`, verifier output,
  and command output never enter it. The command preambles show decision counts
  instead of the whole log; `status` reads only `architecture.md`'s `Last updated:`
  line and the latest report's Summary, and `redo` edits only that stage's decision
  entries.
- **`yolo`'s reset section is rewritten as *Staying lean across stages*.** It no
  longer claims the root can drop material between stages: leanness comes from
  never loading it, with each stage's context coming from `handoff.py`. The safety
  valve stays — if a run still grows long, it stops at a stage boundary and asks
  for `/clear` and a re-run, which resumes from `STATE.md`.
- **`analyze-tokens`** wording: a high orchestrator share now names spec files read
  in to paste as a likely cause. Metrics are unchanged.

## [1.3.0] — 2026-10-10

Each stage now hands the next one better context: a richer report, and a running
`architecture.md` that describes the code as of the latest `done` stage instead of
being written only at cleanup.

### Added

- **`## Behaviour as built` and `## Superseded` in the stage report.** The first
  records the rules that actually landed, as invariants — ordering, failure
  handling, exit codes, defaults, what is rejected, edge-case outcomes — taken from
  the contract and corrected to what the verifier confirmed. The second lists, one
  line each, the earlier stage's rule or contract this stage deliberately changed,
  or "none". About 300–500 tokens together. `## Summary` and `## Changes` are
  unchanged, and so is the `stage-runner`'s 800-token return budget: the report
  goes to disk, not up.

### Changed

- **`architecture.md` is updated after every stage.** On `VERDICT: PASS`, before
  committing, the `stage-runner` rewrites `specs/<feature>/architecture.md` from
  the previous version, this stage's report, `DECISIONS.md`, and
  `SESSION_STATE.json` — not from source — and commits it with the stage. Same
  six-section schema and 1,000–1,500 token target as before. Each update describes
  the code as it stands; superseded rules are removed rather than annotated, and
  the history stays in the reports. If an update would run over budget, prose is
  cut, never contracts. The header now says the file is updated after every stage,
  is authoritative as of the latest `done` stage, and names the stage that last
  updated it.

- **The stage handoff.** For stage N > 1, `next`, `yolo`, and cleanup's
  remediation stage pass the `stage-architect` and `stage-runner` the full
  `architecture.md` and the full report of the immediately previous stage, instead
  of the Summary and Changes sections of every earlier report. The `stage-runner`
  forwards `architecture.md` to the `implementer`; the `verifier` still receives
  only the contract and the diff. `yolo`'s between-stage reload is updated to
  match. A feature started before this change, with no `architecture.md` yet, falls
  back to the old handoff until its next stage passes and creates the file.

- **The `stage-runner` writes the report before committing**, so the report and
  `architecture.md` land in the stage's commit. The report's `Commit:` line is
  filled in afterwards and rides along with the next commit.

- **`/staged-build:cleanup` finalises `architecture.md` rather than writing it.**
  It applies the reviewed decision statuses and marks the header final. A feature
  with no `architecture.md` still gets one written from scratch, as before.

- **`status`** shows whether `architecture.md` exists and which stage last updated
  it. **`redo`** notes that its reset (or `jj abandon`) already reverts the file,
  since it is committed with each stage.

- **`analyze_tokens.py`** wording: a feature with no `architecture.md` is described
  as predating the per-stage file, with `/staged-build:cleanup` to write it.
  Metrics are unchanged.

## [1.2.0] — 2026-10-05

### Changed

- **Stage size guideline is now ~500 lines of diff, excluding tests, and soft.**
  It was a flat ~400 including tests. The `plan-architect` and `stage-architect`
  now cut stages along the seams of the change — one concern, one reason to revert —
  rather than at a line count. A stage that bundles unrelated work is split even
  when small; a stage whose pieces only make sense together (an interface and its
  consumers, a rename and its call sites) stays whole and says in its summary that
  it runs over. The `[large]` step mark at ~150 lines is unchanged.

## [1.1.0] — 2026-09-08

Cuts the pipeline's context amplification, and gives a finished feature a single
permanent reference instead of twenty fragments. Ports the two most recent updates
from the Gemini counterpart
([nsrandom/gemini-staged-build](https://github.com/nsrandom/gemini-staged-build)).

Telemetry from two production runs on the Gemini side — 64.5M tokens across 11
stages, and 147.0M across 20 — put four structural costs above 70% of the total
burn: implementers running to 86 turns in one conversation, payload limits that
contradicted each other across files, root orchestrators accumulating 139 turns of
cross-stage history, and downstream work re-deriving finished features from source
because nothing on disk described them.

### Added

- **Implementer turn budget and the relay protocol.** The `implementer` has 25
  turns, with 30 as a hard ceiling. At turn 20 it audits itself, and if it is
  repeating edit → test → same-failure cycles it writes
  `specs/<feature>/stages/NN-slug.wip.md` — criteria already passing, files
  modified, the specific thing it is stuck on and what it has ruled out, planned
  next steps — and returns `STATUS: RELAY_REQUIRED`.

  The `stage-runner` treats that as a normal outcome: it ends that session and
  invokes a **fresh** `implementer` holding only the contract, the checkpoint, and
  the workspace on disk. The relay starts at a few thousand tokens rather than
  inheriting a hundred thousand of stale test scrollback. At most 2 relays per
  stage; a third returns `STATUS: HALTED`, because a stage needing four sessions is
  mis-specified rather than unlucky. The 2 fix-cycle budget is per stage and is not
  reset by a relay.

- **`specs/<feature>/architecture.md`.** At the end of `/staged-build:cleanup` —
  after the Tier 1 batch, the Tier 2 walkthroughs, any remediation stage, and the
  scratchpad deletion — the feature gets one permanent system reference, committed
  with the rest: executive summary and entrypoints; module and directory map;
  public API, CLI contracts, and JSON schemas; key invariants and data flow; an
  exhaustive table of configuration keys and named constants with their `file:line`;
  and the authoritative test command and fixtures. 1,000–1,500 tokens.

  It is synthesised in a single pass from metadata already verified on disk —
  `DECISIONS.md`, the `Summary` and `Changes` sections of the stage reports, and
  `SESSION_STATE.json` — and explicitly **not** by re-reading source files, which
  would make documenting the feature cost as much as building it.

  `/staged-build:stage`, `/staged-build:yolo`, and `/staged-build:cleanup` all
  list the `architecture.md` files that exist, and pass the relevant one down
  verbatim to the `stage-architect` and `implementer` when a stage integrates with
  an already-completed feature. The `plan-architect` reads them before searching
  the codebase, and `analyze_tokens.py` reports which exist and flags a completed
  feature that has none.

- **`specs/<feature>/stages/NN-slug.verification.log`.** The `verifier` now
  redirects every command it runs into this file — it has no Write tool, so it uses
  the shell it already has, appending rather than truncating so a fix cycle's
  re-run sits under the first. Its return message carries a per-command exit-code
  line, the criteria checklist, its findings, and the verdict.

- **`turn-budget` finding** in `analyze_tokens.py`, for an implementer that ran past
  25 turns instead of checkpointing and relaying.

### Changed

- **Return budgets are role-specific, and consistent everywhere.** The plugin
  previously stated 250, 1000, and "no budget" in different files. Now, in the agent
  files, both orchestrator commands, this changelog, the README, and
  `scripts/analyze_tokens.py`:

  | Role | Budget |
  |---|---|
  | `implementer` | ≤350 tokens |
  | `stage-architect` | ≤350 tokens |
  | `verifier` | ≤300 tokens |
  | `stage-runner` | ≤800 tokens |
  | `plan-architect` | ≤800 tokens |

- **Decision rationale left the return payloads.** `implementer` and
  `stage-architect` now return `DECISION_IDS: [D01 (Tier 1)]` — references only.
  The full entries (the call, the alternatives, the tradeoffs, the `Change it here`
  line) go to `NN-slug.report.md` and `NN-slug.detail.md` respectively, and the
  `stage-runner` reads them off disk to transcribe into `DECISIONS.md`, renumbering
  across the feature. `DECISIONS_LOGGED` is gone.

- **`yolo` resets its context at every stage boundary.** On `STATUS: PASS` the root
  orchestrator drops the finished stage's material — delegation prompt, pasted file
  contents, runner summary — and reloads from exactly four files: `STATE.md`,
  `DECISIONS.md`, `SESSION_STATE.json`, and the previous stage reports' Summary and
  Changes sections, plus any relevant `architecture.md`. Carrying a completed
  stage's history forward is prohibited: it is what turned a long run into a
  139-turn, ~10M-token root session. If a run grows long anyway, it stops at a stage
  boundary and asks for a `/clear` and a re-run, which resumes from `STATE.md`.

- **`retry-loop` and `retry-budget` are relay-aware.** Implementer re-entries are
  now reported as fix cycles or relays rather than assumed to be fix cycles, and the
  critical threshold is 5 invocations (one initial, at most two relays, at most two
  fix cycles).

- **`redo`** also deletes the stage's `.wip.md` and `.verification.log`.

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
