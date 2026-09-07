---
description: Review the decisions taken without asking, then run a cleanup stage for whatever you reject and to clear the scratchpad.
argument-hint: (nothing - use the only feature) | --feature <name>
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, TodoWrite, AskUserQuestion
model: opus
disable-model-invocation: true
---

# Cleanup

Requested: `$ARGUMENTS`

## Resolved context

This command deliberately starts in a **fresh, lean context**. Everything it needs
is on disk, loaded below. Do not go looking for the run that produced it: not the
diffs, not the stage reports, not the test logs. A twenty-stage run leaves a
transcript that would swamp this conversation, and none of it is needed to ask a
user whether they agree with a decision.

- Routing config:
!`cat .claude/pipeline.json 2>/dev/null || cat "${CLAUDE_PLUGIN_ROOT}/pipeline.json"`
- Feature plans in `specs/`: !`ls -1 specs 2>/dev/null || echo "(none yet)"`
- Is a jj repo: !`test -d .jj && echo yes || echo no`
- Current git branch: !`git branch --show-current 2>/dev/null || echo "(n/a)"`
- Uncommitted code (excluding `specs/`): !`git status --porcelain -- ':!specs' 2>/dev/null || echo "(n/a)"`

### Session state

!`cat specs/*/SESSION_STATE.json 2>/dev/null || echo "(none yet)"`

### Plan state

!`cat specs/*/STATE.md 2>/dev/null || echo "(no STATE.md)"`

### Decision log

!`cat specs/*/DECISIONS.md 2>/dev/null || echo "(none yet)"`

### Scratchpad

!`find specs/*/scratchpad -type f 2>/dev/null | head -50 || echo "(none)"`
!`du -sh specs/*/scratchpad 2>/dev/null || true`

### Feature goal and approach

!`sed -n '1,60p' specs/*/SPEC.md 2>/dev/null || echo "(no SPEC.md)"`

---

# Your role

You are the root orchestrator, running the last stage of a feature: the one where
the user finds out what was decided in their absence and says whether they agree.

Resolve `<feature>` from `$ARGUMENTS` or from the listing above. If several
features exist and none was named, ask which. Never guess.

**You write no code.** If remediation is needed, it becomes a real stage and goes
through `staged-build:stage-runner` like every other stage.

If `DECISIONS.md` has no `unconfirmed` entries and there is no `scratchpad/`
directory, say so and stop — there is nothing to clean up.

---

# 1. Sort the decisions by tier

Read `DECISIONS.md` and split the `unconfirmed` entries:

- **Tier 1 — routine conventions.** Naming, placement inside an existing pattern, a
  default constant or timeout, test layout and fixture shape, a log or error
  message, a CLI flag alias.
- **Tier 2 — substantive behaviour.** Strict versus lenient validation, evaluation
  or short-circuit ordering, an addition to a public surface, fallback behaviour
  when something optional is absent.

Most entries carry a `Tier:` line already. Where one is missing or is obviously
wrong, classify it yourself and say you did. When you are unsure, treat it as
Tier 2 — that costs the user a paragraph, and the opposite mistake costs them a
behaviour they never agreed to.

The split is the whole economy of this review: fifteen naming decisions confirmed
in a single click, and the four that actually shape the software given real
attention.

# 2. Tier 1: one batch review

Present every Tier 1 decision as a single table — ID, title, the call, and the
`file:line` that holds it — then ask once:

> *N routine decisions followed existing conventions. Confirm all N, or pick any
> you want to look at more closely.*

Use AskUserQuestion with `multiSelect: true`, offering "Confirm all N
(Recommended)" first, and the individual decisions as the other options, in batches
of four questions if there are many. Say plainly that confirming all of them is a
fine answer.

- Confirmed → mark `confirmed` in `DECISIONS.md`.
- Picked for a closer look → add to the walkthrough queue below.

Do not summarise the table into a paragraph. The point of the review is that the
user sees each one, even the ones they wave through.

# 3. Tier 2 and flagged decisions: one at a time

Walk the Tier 2 decisions and anything the user flagged, **one at a time**. For
each, present three things in the log's own words:

- **The problem** — what was genuinely undecided when the code was written.
- **What was decided** — the choice, and exactly where it lives:
  `Change it here: path/to/file.ext:line`.
- **Tradeoffs and implications** — the alternatives passed over, what this costs,
  and what it constrains later.

Then offer five options:

1. **Confirm** — mark `confirmed`.
2. **Reject** — mark `rejected`, and record what the user wants instead.
3. **Defer** — add to the deferred queue and come back to it after the pass.
4. **Ask a follow-up** — answer it, elaborate, then present the same five options
   again.
5. **Modify** — mark `modified: <details>` with the adjustment they want.

Write each status into `DECISIONS.md` as you go, not at the end. If this
conversation stops halfway, the file must still say exactly where the review got
to.

When the pass finishes, work through the deferred queue the same way until it is
empty.

# 4. Decide whether a cleanup stage is needed

A cleanup stage is needed if **either**:

- any decision ended up `rejected` or `modified`, or
- `specs/<feature>/scratchpad/` exists.

If neither is true, say every decision is confirmed and nothing is left to remove,
commit the updated `DECISIONS.md` with a `<feature>-stage-NN: ` prefixed message,
and stop.

# 5. Run the cleanup stage

1. **Add the row.** Append a stage to `STATE.md`, numbered one past the highest
   existing row, titled `Cleanup and decision remediation`, and mark it
   `in-progress`.

2. **Write down what it must do**, so the stage-architect has a real brief. In a
   short section appended to `SPEC.md` under the new stage, list:

   - each `rejected` decision, its `file:line`, and what the user wants instead;
   - each `modified` decision, its `file:line`, and the adjustment;
   - deletion of `specs/<feature>/scratchpad/` and any scratch databases or
     temporary artifacts it created outside that directory;
   - that the full test suite must still pass afterwards, with no coverage lost.

3. **Check the tree and the branch.** Same rules as any stage: nothing outside
   `specs/` may be dirty, and the run stays on the `Branch:` from `STATE.md` —
   never `main`/`master`. In a jj repo, `jj new -m "<feature>-stage-NN: cleanup"`.

4. **Specify it.** Delegate to `staged-build:stage-architect` with the full text of
   `SPEC.md` (including the section you just wrote), the new STATE.md row, and the
   Summary and Changes sections of the previous stage reports. It writes
   `NN-cleanup.md` and `NN-cleanup.detail.md`.

5. **Show the user the cleanup plan** — the acceptance criteria, the verification
   command, and the files it will touch — and get their go-ahead, exactly as
   `/staged-build:stage next` does. Revise via the stage-architect if they want
   changes.

6. **Run it.** Delegate to `staged-build:stage-runner` with `MODE: supervised`, the
   approved `NN-cleanup.md` and `NN-cleanup.detail.md`, `SPEC.md`, `STATE.md`,
   `SESSION_STATE.json`, the routing config, and the prior report summaries.

   The verifier will confirm the remediations landed, that
   `specs/<feature>/scratchpad/` is gone, and that the test suite still passes.

7. **On `STATUS: PASS`:**
   - Mark the row `done` in `STATE.md`.
   - Update the remediated entries in `DECISIONS.md`:
     `rejected → remediated in stage NN`, `modified → remediated in stage NN`.
   - The stage-runner has already committed. Report the commit.

   On `HALTED` or `REPLANNED`, stop and report it exactly as the other commands do:
   which step, the blocker in the subagent's own words, and the state of the tree.

---

# Reporting

Close with:

- how many decisions were confirmed, rejected, modified, and remediated, by tier;
- whether the scratchpad is gone;
- the cleanup commit, if there was one;
- *"Feature complete. To inspect token consumption, latency, and subagent
  efficiency, run `/staged-build:analyze-tokens`."*

Report verdicts as the subagents gave them. A cleanup stage that fails verification
is a failed stage — do not wave it through because it is "only cleanup".
