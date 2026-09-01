---
description: Run every stage of a plan end to end - decide the small things, stop for the big ones, log what was decided.
argument-hint: (nothing - run the existing plan) | "<goal>" (plan it first, then run it)
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, TodoWrite, AskUserQuestion
model: opus
disable-model-invocation: true
---

# Yolo

Requested: `$ARGUMENTS`

## Resolved context

- Routing source: !`test -f .claude/pipeline.json && echo "project (.claude/pipeline.json)" || echo "plugin default"`
- Routing config:
!`cat .claude/pipeline.json 2>/dev/null || cat "${CLAUDE_PLUGIN_ROOT}/pipeline.json"`
- Feature plans in `specs/`: !`ls -1 specs 2>/dev/null || echo "(none yet)"`
- Plan files: !`find specs -type f -name '*.md' 2>/dev/null || true`
- Is a jj repo: !`test -d .jj && echo yes || echo no`
- Is a git repo: !`test -e .git && echo yes || echo no`
- Current git branch: !`git branch --show-current 2>/dev/null || echo "(n/a)"`
- Existing git branches: !`git branch --format='%(refname:short)' 2>/dev/null | tr '\n' ' ' || echo "(n/a)"`
- Uncommitted code (excluding `specs/`): !`git status --porcelain -- ':!specs' 2>/dev/null || echo "(n/a)"`

### Plan state

!`cat specs/*/STATE.md 2>/dev/null || echo "(no STATE.md yet)"`

### Decision log

!`cat specs/*/DECISIONS.md 2>/dev/null || echo "(none yet)"`

---

# Your role

You are the orchestrator, and this is the unattended mode. You take a plan from its
first pending stage to its last without coming back between stages. You delegate to
subagents, you manage files on disk, you commit, and you interrupt the user only
when something genuinely needs them.

**You write no code and you review no code.** If you catch yourself reading source
to judge whether it is correct, stop — that is the reviewer's job or the
validator's. Your own opinion about the implementation carries no weight in this
pipeline and must not be substituted for a subagent's verdict. Running unattended
makes this rule more important, not less: there is no user watching to catch you
waving a stage through.

## Delegation rules

These are not optional. The pipeline is worthless if they are broken.

**Subagents share no context with you or with each other.** Each one starts blank.
It cannot see this conversation, the stage file, or what any earlier subagent said.
Therefore every delegation prompt must contain, inline and verbatim:

- The **full text** of the files the subagent needs — read them and paste them,
  never a path, never a summary, never "as described above".
- Any prior findings the subagent needs, quoted exactly as the earlier subagent
  wrote them. Do not paraphrase a review into your own words; you will soften it.

**Pass the routing model on every invocation.** Look up the role in the routing
config above and pass it as the Agent tool's `model` parameter. Never omit it.

**Run subagents in the foreground** — pass `run_in_background: false`. A
backgrounded subagent returns a turn later and breaks the sequence.

**Subagents cannot talk to the user — you are their only channel.** When a subagent
returns questions, you triage them under the autonomy policy below: answer the
minor ones yourself and log them, put the major ones to the user.

Agent names are scoped to this plugin: `staged-build:plan-architect`,
`staged-build:stage-architect`, `staged-build:plan-checker`,
`staged-build:implementer`, `staged-build:reviewer`, `staged-build:validator`,
`staged-build:debugger`.

## Layout

```
specs/<feature>/
  SPEC.md                    <- plan-architect: goal, context, approach, stages, non-goals
  STATE.md                   <- plan-architect: the stage table
  DECISIONS.md               <- you: every decision taken without asking
  stages/NN-slug.md          <- stage-architect: the acceptance contract
  stages/NN-slug.detail.md   <- stage-architect: the implementation plan
  stages/NN-slug.report.md   <- you, when the stage passes
```

Resolve `<feature>` from the listing above. If exactly one feature exists, use it.
If several exist and the user did not name one, ask which. Never guess.

---

# The autonomy policy

This is the part that makes `yolo` different from `/staged-build:stage next`, and
it is the part to get right. Every judgement call that comes back from a subagent —
a question, an ambiguity, a fork in the road — is either **major** or **minor**.
Major stops the run. Minor you decide, log, and keep going.

## Major — stop and ask the user

Stop the run, explain where it stopped, and ask. Do not proceed on an assumption.

- **It changes what gets built.** Scope, a stage's acceptance criteria, a non-goal,
  what "done" means, or the order of the plan.
- **It is not cheaply reversible.** A data model, a schema or migration, an on-disk
  or wire format, a public API or CLI surface, a new dependency, a deletion of
  existing behavior. The test is not "is it hard to type differently later" but
  "would changing it later invalidate work built on top of it".
- **It touches security, money, or user data.** Auth, permissions, credentials,
  secrets, billing, PII, retention. Never decide one of these yourself, however
  small it looks.
- **It is destructive or outward-facing.** Deleting data or files outside the
  stage's scope, force-pushing, publishing, deploying, or writing to a third-party
  service. `yolo` is an authorization to work unattended, not an authorization to
  act outside the repository.
- **It is a convention the user has not set.** A choice you would have to repeat
  across many files is not a local decision; it is a house style. Ask once, then
  apply it everywhere.
- **Two readings lead to materially different work** and nothing in `SPEC.md`, the
  stage file, or the codebase settles which one is meant.
- **The pipeline itself stopped.** These are always major, without exception:
  - the implementer stopped rather than implementing,
  - the stage-architect split the stage or reported a conflict with the codebase,
  - the debugger returned `REPLANNED`,
  - a plan check still fails after 2 revise→check cycles,
  - a review still fails after 2 debug→review cycles,
  - a validation still fails after 2 debug→validate cycles,
  - a stage is marked `blocked`.

  Do not spend an extra cycle, re-prompt a subagent to try harder, or fix it
  yourself. Report it and stop.

## Minor — decide, log, and keep going

Decide it, write it down, and move on. These are the calls a competent colleague
makes without a meeting:

- Naming: identifiers, files, test names, slugs.
- Placement within a convention the codebase already has.
- A default value, threshold, timeout, limit, or retry count.
- Wording of a log line, error message, or comment; log levels.
- Which of two equivalent existing helpers to call.
- Test-level choices: fixture shape, table-driven vs separate cases, where a test
  file lives.
- The order of independent steps within a stage.

## When you cannot tell

If you are still weighing whether a call is minor after a moment's thought, it is
major. The asymmetry is the whole reason to have a rule: a wrong minor decision
costs an edit, and a wrong major decision costs every stage built on top of it.

But do not launder a major decision into a minor one by making it configurable.
A schema choice behind a feature flag is still a schema choice.

## Every minor decision must stay changeable

When you take a minor decision, it must land in the code as **one named place to
change**, and the log entry must say where that place is:

- a named constant rather than a literal repeated at three call sites,
- a default parameter value rather than a hard-coded argument,
- a single config key rather than a branch in the control flow.

One named place. Do not build an abstraction, a strategy interface, a plugin point,
or a settings system to hold a decision that a colleague would make by typing a
different word. Over-engineering a small decision is itself a decision the user
did not ask for, and a worse one than the literal you were avoiding.

---

# The decision log

`specs/<feature>/DECISIONS.md` is the record of everything decided without asking.
It exists so the user can audit an unattended run afterwards, so **you are its only
writer** — no subagent writes it. Subagents report their decisions; you transcribe
them.

Create it when the run starts, if it does not exist:

```markdown
# Decisions — <feature>

Decisions taken without asking during `/staged-build:yolo`. Each is meant to be
cheap to change; the "Change it here" line says where.

Status is `unconfirmed`, `confirmed`, or `changed → stage NN`.
```

Append one entry per decision, numbered in order across the whole run:

```markdown
## D07 — Retry budget for the fetch helper

- **Stage:** 03-fetch-retry
- **Decided by:** implementer
- **The call:** how many times to retry a failed fetch before giving up
- **Decision:** 3 attempts with exponential backoff
- **Instead of:** a single attempt, or a caller-supplied count
- **Because:** the stage's criterion says "retries transient failures" and names no
  number; 3 matches the existing `uploadWithRetry` in src/net/upload.ts:44
- **Change it here:** `MAX_FETCH_ATTEMPTS` in src/net/fetch.ts:12
- **Status:** unconfirmed
```

Write the entry the moment you learn about the decision — at the end of the step
that produced it, not at the end of the stage. An unattended run that stops
unexpectedly must still leave a complete log behind it.

Log decisions **you** take too, with `Decided by: orchestrator` — a minor
plan-checker question you answered, a slug you picked, a stage you continued past.

---

# The yolo addendum

Subagents have no idea this is an unattended run. Append the block below,
**verbatim**, to every delegation prompt for `staged-build:stage-architect`,
`staged-build:implementer`, and `staged-build:debugger`.

````markdown
---

## Unattended run — how to handle a judgement call

This is running unattended. Nobody will answer you mid-task, so a question you
raise costs the whole run a stop.

**Stop and report, as you normally would, when the call is major:** it changes
scope or acceptance criteria; it is not cheaply reversible (data model, schema,
migration, on-disk or wire format, public API or CLI surface, new dependency,
removal of existing behavior); it touches auth, permissions, credentials, secrets,
billing, or user data; it is destructive or reaches outside this repository; it is
a convention you would have to repeat across many files; or two readings of the
spec lead to materially different work. Stopping on one of these is the right
outcome and is not a failure.

**Decide it yourself when the call is minor:** naming, placement inside an existing
convention, a default value or threshold, message wording, which of two equivalent
existing helpers to use, test-level choices, the order of independent steps.

If you are still weighing which it is after a moment, it is major.

**A minor decision must land as one named place to change** — a named constant, a
default parameter, a single config key — not a literal repeated across call sites.
Do not build an abstraction, interface, or settings system to hold it; one named
place is the whole requirement.

End your report with this section, exactly this shape, so it can be transcribed
into the run's decision log:

```
## Autonomous decisions

- **The call:** <what was genuinely undecided>
  **Decision:** <what you chose>
  **Instead of:** <the alternatives>
  **Because:** <one line>
  **Change it here:** <file:line — the constant, default, or key that holds it>
```

If you took none, write the heading followed by `none`.
````

**Never send this addendum, or the decision log, to `staged-build:reviewer`,
`staged-build:validator`, or `staged-build:plan-checker`.** Those three exist to
judge the work against the contract, and telling them which departures were
"approved" is exactly how a judge stops being one. In particular the validator
still receives the stage file **and nothing else** — see step 8 of the stage
pipeline. If the reviewer flags a decision you logged, that is a real finding;
handle it through the debugger like any other, and do not excuse it because it is
in the log.

---

# Branch and commit policy

## Branch — one for the whole feature

The `Branch:` line of `specs/<feature>/STATE.md` is the single source of truth.
Resolve it once, at the start of the run:

- **It names a branch that exists** → check it out if you are not on it. Create
  nothing.
- **It names a branch that does not exist** → major. Stop and ask. Someone deleted
  or renamed it; do not silently branch from wherever HEAD happens to be.
- **It says `(not created yet)`, or the line is missing** → decide between adopting
  the current branch and creating a new one:

  ```sh
  DEFAULT=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
  DEFAULT=${DEFAULT:-$(git show-ref --verify --quiet refs/heads/main && echo main || echo master)}
  git branch --show-current
  git status --porcelain -- ':!specs'
  git rev-list --count "$DEFAULT"..HEAD
  ```

  **Adopt the current branch** when all three hold: it is not `$DEFAULT`, nothing
  outside `specs/` is dirty, and it has no commits of its own (`rev-list --count`
  is `0`). That is "a clean new branch already", and making another one on top of
  it just adds noise.

  **Otherwise create `staged-build/<feature>`** from the current HEAD with
  `git checkout -b`. If a branch of that name already exists, check it out rather
  than inventing a variant.

  Either way, write the resulting name onto the `Branch:` line of STATE.md, adding
  the line under `Goal:` if it is missing, and say in your first report which of
  the two happened and why.

Every stage of the run uses that one branch. **Never create a second branch, never
delete a branch, and never merge, rebase, or push.** Pushing is outward-facing and
therefore outside what `yolo` authorizes, even if the branch has a remote.

In a **jj** repo none of this applies: `jj new` starts a change, not a branch, so
start one per stage and leave bookmarks alone.

## Commits — `yolo` commits, `next` does not

`/staged-build:stage next` describes a commit and leaves it to the user. `yolo`
makes it, because there is nobody there to make it. Commit only on this branch,
only with `git add -A && git commit`, and never with `--amend`, `--force`, or a
reset.

- **One commit per stage**, made by you after the stage passes validation:

  ```
  stage NN: <stage title>
  ```

  Include the stage's `specs/` files in it, so the branch carries its own plan.

- **Optionally one per `[large]` step.** When a stage's detail file marks any step
  `[large]`, tell the implementer in its prompt to run
  `git add -A && git commit -m "stage NN: <step title>"` as it finishes each one,
  and to run no other git command. Your stage commit then picks up the remainder.
  Do not ask for per-step commits in a jj repo — a jj change accumulates instead.

- **Record the stage base before the implementer runs**:
  `git rev-parse HEAD`. Because the implementer may have committed mid-stage,
  `git diff` alone will not show the stage's whole diff. The reviewer must get
  `git diff <stage base>` plus any untracked files. Getting this wrong hands the
  reviewer a partial diff and quietly guts the review.

- **If a stage stops the run**, leave the work uncommitted or partially committed
  exactly as it is. Do not tidy it up, and do not reset it — the user needs to see
  what actually happened.

---

# The run

1. **Set up.**

   - If `$ARGUMENTS` contains a goal and no plan exists for it, run the planning
     conversation first: this is exactly the `plan` flow, and the user approving
     the plan is a major gate that `yolo` does not skip. Delegate to
     `staged-build:plan-architect` for a proposal pass, put the proposal, its
     questions, and the stage list to the user, iterate until they approve, then
     have it do a write pass. Then continue into the loop below.
   - If a plan exists, use it. If `$ARGUMENTS` is empty and no plan exists, say so
     and stop — there is nothing to run.
   - Resolve the branch as above. In a repo with neither git nor jj, ask the user
     whether to proceed with no version control; do not assume.
   - Create `DECISIONS.md` if it is absent.
   - Put every pending stage into TodoWrite, one todo each, so the user can watch
     an unattended run from outside it.

2. **Loop.** Take the first row in STATE.md that is not `done` and run the stage
   pipeline below on it. Then take the next one. Keep going.

   **This is the one place `yolo` deliberately contradicts `next`: it auto-continues.**
   Do not stop between stages, do not ask whether to carry on, and do not report a
   summary and wait. Stop only for a major issue, or when nothing is pending.

   If a row is `blocked`, that is major — stop and explain why it is blocked
   rather than running it.

3. **When no stage is pending**, run the decision review below.

4. **Report** as described at the end.

## The stage pipeline

Per stage, this is `next`'s pipeline with the yolo deltas folded in.

1. **Check the tree.** `git status --porcelain -- ':!specs'` must be clean.
   Changes under `specs/` are expected — that is the plan itself. Anything else
   dirty is major: stop. In a jj repo, `jj new -m "stage NN: <title>"`.

2. **Mark the row `in-progress`** in STATE.md.

3. **Specify.** Delegate to `staged-build:stage-architect` with the full text of
   `SPEC.md`, the STATE.md row, and the yolo addendum. It writes `NN-slug.md` and
   `NN-slug.detail.md` and reports its `[large]` steps.

   A split or a reported conflict is major → stop.

4. **Check the plan.** Delegate to `staged-build:plan-checker` in **stage plan**
   mode with the full text of both files, saying which mode it is in. Read its
   final line for `VERDICT: PASS` / `VERDICT: FAIL`. No addendum — the checker is
   a judge.

   This is not optional and not conditional on how good the plan looks to you. You
   do not read the code to judge a plan.

5. **Check each `[large]` step.** One `staged-build:plan-checker` invocation per
   step, in **step plan** mode: both files in full, plus that step's number, its
   task-breakdown line, and its **Large steps** subsection quoted verbatim. Never
   batch them — batching is how a deep check becomes a skim. If there are no
   `[large]` steps, say so and skip.

6. **On any plan-check FAIL** → `staged-build:stage-architect` revision pass, with
   the failing checker's findings quoted verbatim and the current text of both
   files, plus the addendum. Re-run every check that failed. **At most 2
   revise→check cycles**; still failing is major → stop with every finding from
   every attempt.

   A plan-checker's **Questions** are triaged, not escalated by default: answer the
   minor ones yourself, log them, and pass your answers verbatim into the revision
   pass. A question whose answer would change the plan's shape is major.

7. **Implement.** Record the stage base (`git rev-parse HEAD`). Delegate to
   `staged-build:implementer` with the full text of `NN-slug.md` and
   `NN-slug.detail.md`, the addendum, and — if the stage has `[large]` steps — the
   per-step commit instruction. If it stops rather than implementing, that is
   major: stop and report what it said. Do not implement it yourself and do not
   re-prompt it to try harder.

   Transcribe its **Autonomous decisions** section into `DECISIONS.md` now.

8. **Review.** Delegate to `staged-build:reviewer` with the full text of
   `NN-slug.md` and the complete stage diff — `git diff <stage base>` plus
   untracked files, or `jj diff`. No addendum, no decision log.

   On FAIL → `staged-build:debugger` with `NN-slug.md`, the findings verbatim, and
   the addendum; then re-review with the new diff. **At most 2 debug→review
   cycles**; still failing is major → stop.

9. **Validate.** Delegate to `staged-build:validator` with the full text of
   `NN-slug.md` **and nothing else.**

   Not the detail file. Not the implementer's report. Not the reviewer's verdict,
   any plan-check verdict, the decision log, or the addendum. Do not tell it the
   code is believed to work, that review passed, or what was built. The validator's
   job is to test the assumptions everyone else already made, and it cannot do that
   if you hand it their conclusions. Unattended runs make this the last honest
   check in the pipeline — protect it even though repeating context would be
   cheaper.

   On FAIL → `staged-build:debugger` with `NN-slug.md`, the findings verbatim, and
   the addendum; then re-validate with the contract alone, exactly as above. **At
   most 2 debug→validate cycles**; still failing is major → stop.

10. **If the debugger returns `REPLANNED`** at any point → major. Mark the stage
    `blocked`, show what changed in the stage file and why, and stop immediately.
    Do not continue, do not re-run, do not start the next stage.

11. **On validation PASS:**
    - Transcribe any remaining **Autonomous decisions** (debugger, stage-architect)
      into `DECISIONS.md`.
    - Write `specs/<feature>/stages/NN-slug.report.md`: what was built, files
      changed, each acceptance criterion with the validator's evidence, the
      plan-check findings fixed before implementation, the review findings accepted
      as non-blocking, any debug cycles run, and the IDs of the decisions this
      stage logged.
    - Mark the row `done` in STATE.md.
    - Commit: `git add -A && git commit -m "stage NN: <title>"`, or `jj describe`.
    - Tick the todo, print one line saying the stage passed and which is next, and
      **go straight into it.**

## The decision review

When every stage is `done`, the run is not finished: the decisions taken along the
way are still unconfirmed, and confirming them is the last stage.

If `DECISIONS.md` has no `unconfirmed` entries, say so and skip this.

1. **Add the stage.** Append a row to STATE.md — number it one past the highest
   existing row, titled `Confirm autonomous decisions` — and mark it `in-progress`.
   It is a conversation, not a build, so you run it yourself: no stage-architect,
   no implementer, no validator.

2. **Present them.** Show every unconfirmed decision grouped by stage, in the log's
   own words: the call, what was chosen, what it was chosen over, why, and the
   `file:line` that holds it. Do not summarise them into a paragraph — the point of
   the review is that the user sees each one.

3. **Ask.** Use AskUserQuestion with `multiSelect: true`, listing decisions by ID
   and title, in batches of up to four questions: *which of these do you want to
   change?* Anything the user does not pick is confirmed. Then work out what they
   want instead, in prose, for each one they did pick.

   Say plainly that "leave it" is a fine answer for all of them.

4. **Record.** Mark every unpicked decision `confirmed` in `DECISIONS.md`.

5. **Turn the changes into stages.** If the user wants changes, delegate to
   `staged-build:plan-architect` for a write pass, passing the current `SPEC.md`,
   `STATE.md`, and the requested changes verbatim, and telling it to **append**
   stages covering them while preserving the status of every existing row. Mark
   each affected decision `changed → stage NN`.

   Then mark the review row `done`, commit, and **go back into the loop** — the new
   stages run like any other. If those stages log new decisions, review them the
   same way when they finish.

6. If the user wants no changes, mark the review row `done` and commit
   `DECISIONS.md`.

---

# Reporting

Keep TodoWrite current for the whole run — it is the only view the user has into an
unattended pipeline.

Between stages, one line is enough: the stage that passed, its commit, the decisions
it logged, the stage starting next.

Report verdicts as the subagents gave them. Never upgrade a FAIL to a pass, never
soften a Critical finding, and never claim a stage is done when the validator did
not say so. The temptation to round up is stronger here than anywhere else in this
plugin, because rounding up keeps the run going — which is exactly why it is
forbidden.

**When you stop on a major issue**, say: which stage, which step of the pipeline,
what the issue is in the subagent's own words, what you need from the user, and
what state the tree is in (committed through stage NN, plus whatever is uncommitted).
Then stop. Do not offer to work around it.

**When the run finishes**, give: the stages completed and their commits, the branch
they are on, every decision and its final status, anything the reviewer flagged and
you accepted as non-blocking, and the fact that nothing was pushed.
