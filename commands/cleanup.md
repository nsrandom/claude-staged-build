---
description: Review the decisions taken without asking - Minor ones shown and confirmed, Significant ones asked about in batches - run a cleanup stage for whatever you reject and to clear the scratchpad, then finalise the feature's permanent architecture reference.
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
- Handoff script: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py"`
- Handoff bundles gitignored: !`grep -q 'specs/\*\*/\.handoff-\*' .gitignore 2>/dev/null && echo yes || echo "no - add it"`
- Context carried in from earlier in this session: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/context_check.py" 2>/dev/null || echo "unknown"`

### Session state

!`cat specs/*/SESSION_STATE.json 2>/dev/null || echo "(none yet)"`

### Plan state

!`cat specs/*/STATE.md 2>/dev/null || echo "(no STATE.md)"`

### Decision counts

- Decisions: !`grep -cE '^(## |- )D[0-9]+' specs/*/DECISIONS.md 2>/dev/null || echo "(none yet)"`
- Significant (including old `Tier 2`): !`grep -cE '(\*\*Level:\*\* Significant|\*\*Tier:\*\* 2|\(Tier 2)' specs/*/DECISIONS.md 2>/dev/null || echo "(none yet)"`
- By status: !`grep -hoE '(\*\*Status:\*\* |\(Tier [12], |^- D[0-9]+ .*· )[a-z]+' specs/*/DECISIONS.md 2>/dev/null | sed -E 's/.* //' | sort | uniq -c | tr -s ' \n' ' ' || echo "(none yet)"`

### Decision index

The log is never read whole. This is a grep of it: every one-line Minor entry, and
for every full entry its heading and its level, status, and location lines, each
with its line number.

!`grep -nE '^- D[0-9]+ ·|^## D[0-9]+|\*\*(Level|Tier|Status|Change it here):\*\*' specs/*/DECISIONS.md 2>/dev/null || echo "(none yet)"`

### Feature architectures

!`ls -1 specs/*/architecture.md 2>/dev/null || echo "(none)"`

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

**Check for a stale context first.** Read the *Context carried in* line above.
Cleanup assumes a fresh context: it re-sends everything in this conversation on
every turn of the review. If the line starts with `large`, tell the user, quoting
it, and recommend a `/clear` before continuing; ask with AskUserQuestion:

- **Stop so I can `/clear`** — stop here, changing nothing. After `/clear`,
  `/staged-build:cleanup` picks up from disk exactly where it would have now; any
  statuses already written stay written.
- **Continue anyway** — carry on.

Ask once. On `fresh`, `small`, or `unknown`, say nothing and carry on.

If the *Decision index* shows no `unconfirmed` entries and there is no
`scratchpad/` directory, say so — there is nothing to review and nothing to remove.
Skip to section 6 rather than stopping: the feature's architecture reference still
needs finalising, or writing if it does not have one yet.

---

# 1. Sort the decisions by level

Work from the *Decision index* above, never from the whole file. Each decision is
one of two levels:

- **Minor** — a routine convention: naming, placement inside an existing pattern, a
  default constant or timeout, test layout and fixture shape, a log or error
  message, a CLI flag alias. Logged as one line under `## Minor decisions` — or, in
  a log from before 1.5.0, as a full entry marked `Tier: 1` / `(Tier 1, …)`.
- **Significant** — substantive behaviour: strict versus lenient validation,
  evaluation or short-circuit ordering, an addition to a public surface, fallback
  behaviour when something optional is absent. A full entry marked
  `Level: Significant` — or, in an older log, `Tier: 2` / `(Tier 2, …)`.

Where an entry has no level, classify it yourself and say you did. When you are
unsure, treat it as Significant — that costs the user a paragraph, and the opposite
mistake costs them a behaviour they never agreed to.

Minor logging exists partly as a safety net against misclassification, so check
it: **a Minor decision that touches logging, masking, or retention of user data, or
that leaves a known defect unfixed or deferred, is Significant.** Move each one you
find to section 3, and say which and why.

# 2. Minor: confirmed automatically

The user always confirms Minor decisions as they are, so do not ask about them.
Show every Minor decision as a single table — ID, title, the decision, and the
`file:line` that holds it, all from the index (an old-format entry's title is its
heading) — for information. Then:

- Mark any Minor entry that is still `unconfirmed` (older logs only — one-line
  entries are logged `confirmed`) as `confirmed`, in one write.
- Say in one line that the user can name any ID to reopen it. If they do, at any
  point in this review, treat that decision exactly like a Significant one in
  section 3.

Do not summarise the table into a paragraph. The point is that the user sees each
one, even though nobody asks them to click.

# 3. Significant decisions: in batches

**Read only what is unconfirmed.** For each `unconfirmed` Significant entry — and
any Minor entry the user reopened or you moved here — Read just that entry: its
line range from the index, from its `## D` heading to the line before the next one.
Never load the rest of the log.

Then walk them **in batches of up to four**, one AskUserQuestion call per batch.

1. **Above the call**, for each decision in the batch, present three things in the
   log's own words:

   - **The problem** — what was genuinely undecided when the code was written.
   - **What was decided** — the choice, and exactly where it lives:
     `Change it here: path/to/file.ext:line`.
   - **Tradeoffs and implications** — the alternatives passed over, what this
     costs, and what it constrains later.

2. **In the call**, each decision is its own question, headed with its ID, with
   four options — Other is always added, and is how the user asks a follow-up or
   says something else:

   1. **Confirm** — mark `confirmed`.
   2. **Reject** — mark `rejected`, and record what the user wants instead.
   3. **Defer** — come back to it after the pass.
   4. **Modify** — mark `modified: <details>` with the adjustment they want.

   A follow-up gets answered, and the decision goes into the next batch with the
   same options. A Reject or Modify that does not say what the user wants goes
   into the next batch as a question asking exactly that.

3. **Write each batch's statuses in the same turn as the next batch's question.**
   Once the answers come back, send the `DECISIONS.md` edits for that batch and the
   next batch's AskUserQuestion in one message — the edits first, then the
   question — rather than one turn per write. Every turn of this review re-sends
   the whole conversation, so a turn spent on a lone edit is the most expensive
   kind there is. After the last batch, write its statuses in one turn of their
   own. Either way the file records every answer before the next question is
   asked: if this conversation stops halfway, it still says exactly where the
   review got to.

   Change the `**Status:**` line of a full entry, or the status in the heading of
   an older `(Tier 2, unconfirmed)` one.

When the pass finishes, work through the deferred ones the same way until none is
left.

# 4. Decide whether a cleanup stage is needed

A cleanup stage is needed if **either**:

- any decision ended up `rejected` or `modified`, or
- `specs/<feature>/scratchpad/` exists.

If neither is true, say every decision is confirmed and nothing is left to remove,
commit the updated `DECISIONS.md` with a `<feature>-stage-NN: ` prefixed message,
and go to section 6.

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

3. **Commit the review before the stage starts.** The stage-runner diffs from the
   commit it starts on, so status edits left uncommitted in `DECISIONS.md` would
   land in the verifier's diff as unrelated changes it has to be told to ignore —
   and a verifier told to ignore part of a diff is no longer judging all of it.
   Commit `DECISIONS.md`, `STATE.md`, and `SPEC.md` now:

   ```sh
   git add specs/<feature>/DECISIONS.md specs/<feature>/STATE.md specs/<feature>/SPEC.md
   git commit -m "<feature>-stage-NN: record the decision review"
   ```

   In a jj repo, `jj describe` the current change with that message and `jj new`.

4. **Check the tree and the branch.** Same rules as any stage: nothing outside
   `specs/` may be dirty, and the run stays on the `Branch:` from `STATE.md` —
   never `main`/`master`. In a jj repo, `jj new -m "<feature>-stage-NN: cleanup"`.

5. **Specify it.** Delegate to `staged-build:stage-architect` by path, exactly as
   `/staged-build:stage next` does: the feature slug, the new stage number and
   title, and the handoff command to run first —

   ```sh
   python3 <handoff script> --feature <feature> --stage NN --role stage-architect --out
   ```

   — which writes `SPEC.md` (with the section you just wrote), `STATE.md`,
   `SESSION_STATE.json`, `architecture.md`, and the previous stage's commit,
   *Behaviour as built*, and *Superseded* to one file for it to Read. Run it with
   `--check` first. Add an `--include` only if the brief itself needs another file.
   It writes `NN-cleanup.md` and `NN-cleanup.detail.md`.

6. **Show the user the cleanup plan** — run the same command with `--role approval`
   (no `--out`) and show the acceptance criteria, the verification command, and the
   files it will touch — and get their go-ahead, exactly as
   `/staged-build:stage next` does. Revise via the stage-architect, with
   `--role stage-runner --out`, if they want changes.

7. **Run it.** Delegate to `staged-build:stage-runner` with `MODE: supervised`, the
   feature slug, stage number, title, and slug, the routing config, and the handoff
   command with `--role stage-runner --out` (checked first). On `VERDICT: PASS` it
   rewrites `architecture.md` as it does for every stage.

   The verifier will confirm the remediations landed, that
   `specs/<feature>/scratchpad/` is gone, and that the test suite still passes.

8. **On `STATUS: PASS`:**
   - Mark the row `done` in `STATE.md`.
   - Update the remediated entries in `DECISIONS.md`:
     `rejected → remediated in stage NN`, `modified → remediated in stage NN`.
   - The stage-runner has already committed. Report the commit.

   On `HALTED` or `REPLANNED`, stop and report it exactly as the other commands do:
   which step, the blocker in the subagent's own words, and the state of the tree.

# 6. Finalise the feature's architecture reference

The feature is now finished, and this is the last thing it owes anyone: a single
permanent reference to what actually landed. **Finalise
`specs/<feature>/architecture.md`** before you report.

It is the answer to a real cost. `SPEC.md` was written before a line of code
existed and has drifted; the stage reports are the truth but they are the truth in
ten or twenty fragments, each describing one slice. So the next agent that touches
this feature — a downstream feature integrating with it, or a maintenance task six
weeks from now — has nowhere to look, and pays for that by reading dozens of source
files to rediscover what a page would have told it.

## If it exists: refresh it

The stage-runner has rewritten `architecture.md` after every stage — the cleanup
stage too, if one ran — so it already describes the code as built. What it cannot
know is how the review ended. Edit it in place:

- **Apply the final decision statuses** from this review. A `rejected` or
  `modified` decision is documented as it ended up after remediation, never as it
  was first taken; drop anything the file still describes as provisional. You
  already hold every status you changed; do not re-read the log for them.
- **Mark it final.** Replace the header paragraph under the title with:

  ```markdown
  Final — refreshed at cleanup after stage NN, once every stage was done and every
  decision reviewed. Until then it was updated after every stage.
  This is the authoritative reference for what this feature is. SPEC.md is what was
  predicted before it was built; the stage reports are the history.
  ```

Do not re-read source files and do not re-synthesise it from the reports — it is
already current, and rebuilding it would make this step the token sink it exists to
prevent. Keep it within the 1,000 to 1,500 token budget below. Read a source file
only to settle a contradiction between the file and `DECISIONS.md`, and say in the
file that you did.

## If there is none: write it from scratch

A feature started before the stage-runner kept a running `architecture.md` reaches
cleanup without one. Write it now.

### Synthesise it, do not re-derive it

**Do not go and read the source files.** Everything you need has already been
verified on disk by someone who ran it, and re-reading the implementation to
document it would make this step the token sink it exists to prevent. Build the
file from exactly three inputs:

- **`specs/<feature>/DECISIONS.md`**, through the *Decision index* above and a
  grep of each entry's `**Decision:**` line rather than a full read — the confirmed
  invariants, named constants, and config keys, each with the file:line that holds
  it. Use the post-review status: a `rejected` or `modified` decision is documented
  as it ended up after remediation, never as it was first taken.
- **The `Summary` and `Changes` sections of every `specs/<feature>/stages/*.report.md`**
  — what each stage landed, with exact names, exports, and types. These were
  written after verification passed, so they describe code that was observed to
  work.
- **`specs/<feature>/SESSION_STATE.json`** — the runtime, the test command, and the
  environment facts.

Read a source file only to settle a contradiction between two of those, and say in
the file that you did.

## The schema

The same schema the stage-runner uses for its per-stage updates. Six sections, in
this order, and **1,000 to 1,500 tokens in total.** It is a cheat sheet, not a
manual: exhaustive about names, contracts, and constants, and terse about
everything else. If it does not fit, cut prose, never contracts.

```markdown
# <feature> — architecture

Final — generated at cleanup from DECISIONS.md, the stage reports, and
SESSION_STATE.json.
This is the authoritative reference for what this feature is. SPEC.md is what was
predicted before it was built.

## 1. Executive summary and entrypoints
<one paragraph: what this feature does, and the primary interfaces through which
anything else reaches it — the module, the CLI command, the endpoint, the export>

## 2. Module and directory map
- path/to/module.ext — <one line: what this file is responsible for>

## 3. Public API, CLI contracts, and JSON schemas
- `functionName(arg: Type, ...) -> ReturnType` — <what it does; what it raises>
- `cmd <sub> [--flag <value>]` — <what it does; exit codes>
- `{ "field": "type" }` — <the shape, and where it is read or written>

## 4. Key invariants and data flow
- <the verified pipeline order, isolation boundary, or constraint — and what
  breaks if it is violated>

## 5. Configuration keys and defaults
| Key | Default | Where it lives | Notes |
|---|---|---|---|
| `MAX_FETCH_ATTEMPTS` | `3` | `src/net/fetch.ts:12` | from D07 |

## 6. Verification and test suite
- Test command: `<the authoritative runner>`
- Runtime: `<interpreter or toolchain>`
- Key fixtures: `<paths>`
```

Section 5 is exhaustive: every environment variable, config parameter, and named
constant that a decision landed as "one named place to change" belongs in it, with
its `file:line`. That table is the single highest-value thing in the file, because
it is what a future agent would otherwise grep the whole tree for.

## Then commit it

`git add specs/<feature>/architecture.md` and commit with the usual
`<feature>-stage-NN: ` prefix — or fold it into the cleanup stage's commit if you
have not committed yet. Running cleanup again later takes the refresh path above;
never append to the file.

Tell the user, in one line, that future features and maintenance sessions should
read `specs/<feature>/architecture.md` as the authoritative source for this
feature, instead of re-reading its stage reports or searching its source.

---

# Reporting

Close with:

- how many decisions were confirmed, rejected, modified, and remediated, Minor and
  Significant separately;
- whether the scratchpad is gone;
- the cleanup commit, if there was one;
- that `specs/<feature>/architecture.md` was finalised (or written, for a feature
  that had none), linked, and what it is for;
- *"Feature complete. To inspect token consumption, latency, and subagent
  efficiency, run `/staged-build:analyze-tokens`."*

Report verdicts as the subagents gave them. A cleanup stage that fails verification
is a failed stage — do not wave it through because it is "only cleanup".
