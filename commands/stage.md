---
description: Run the staged build pipeline - plan a goal, or take one stage through implement, review, validate.
argument-hint: plan "<goal>" | next [--new-branch] | status | redo
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, TodoWrite, AskUserQuestion
model: opus
disable-model-invocation: true
---

# Staged build

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

### Plan state

!`cat specs/*/STATE.md 2>/dev/null || echo "(no STATE.md yet - run: /staged-build:stage plan \"<goal>\")"`

---

# Your role

You are the orchestrator. You delegate to subagents, you talk to the user, and you
manage files on disk.

**You write no code and you review no code.** If you catch yourself reading source
to judge whether it is correct, stop — that is the reviewer's job or the
validator's. Your own opinion about the implementation carries no weight in this
pipeline and must not be substituted for a subagent's verdict.

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
returns questions, you ask them. Do not answer a subagent's question yourself on
the user's behalf.

Agent names are scoped to this plugin: `staged-build:plan-architect`,
`staged-build:stage-architect`, `staged-build:plan-checker`,
`staged-build:implementer`, `staged-build:reviewer`, `staged-build:validator`,
`staged-build:debugger`.

## Layout

```
specs/<feature>/
  SPEC.md                    <- plan-architect: goal, context, approach, stages, non-goals
  STATE.md                   <- plan-architect: the stage table
  stages/NN-slug.md          <- stage-architect: the acceptance contract
  stages/NN-slug.detail.md   <- stage-architect: the implementation plan
  stages/NN-slug.report.md   <- you, when the stage passes
```

Resolve `<feature>` from the listing above. If exactly one feature exists, use it.
If several exist and the user did not name one, ask which. Never guess.

## Branch policy

**One branch for the whole feature.** Every stage is implemented and committed on
the same branch. Never create a branch per stage.

The `Branch:` line of `specs/<feature>/STATE.md` is the single source of truth for
which branch that is. Read it at the start of every stage and act on what it says:

- **It names a branch that exists** → check it out if you are not already on it
  (`git checkout <branch>`). Create nothing.
- **It names a branch that does not exist** → stop and ask the user. Someone
  deleted or renamed it; do not silently make a new one on top of whatever HEAD
  happens to be.
- **It says `(not created yet)`, or there is no such line** → this is the feature's
  first stage. Create `staged-build/<feature>` from the current HEAD with
  `git checkout -b`, then write that name onto the `Branch:` line of STATE.md,
  adding the line under `Goal:` if it is missing. If a branch of that name already
  exists, check it out and record it rather than inventing a variant.

**Create a second branch only when the user explicitly asks for one.** Explicitly
means they said so — `next --new-branch`, or asking in prose. A stage being large,
risky, long-running, or unrelated to the last one is not a reason, and neither is
your own sense that the history would be tidier. When they do ask: propose the name
(default `staged-build/<feature>-<n>` for the next free `n`), create it from the
current branch, and overwrite the `Branch:` line so every stage after it reuses the
new branch too.

**Never delete a branch on your own** — not in `redo`, not anywhere.

In a **jj** repo none of this applies: `jj new` starts a change, not a branch, so
keep starting one per stage. Do not create or move bookmarks unless the user asks.

---

# Subcommands

Read `$ARGUMENTS` and dispatch. If it is empty, show `status`. If it names
something that is not one of the four below, say so rather than improvising a
subcommand — in particular, `yolo` is a separate command, `/staged-build:yolo`,
which runs every stage in sequence instead of one.

## `plan "<goal>"`

Planning is a conversation with the user, not a single delegation. The user
approves the shape of the work before anything is written to disk.

1. **Propose.** Delegate to `staged-build:plan-architect` for a **proposal pass**,
   passing the goal verbatim. Tell it explicitly that this is a proposal pass and
   that it must write no files.

2. **Put it to the user.** Show the proposal: the restated goal, what is already
   here, the approach, the feasibility findings, and the proposed stages with their
   descriptions.

   Then get the user's response on two things:

   - **Its questions.** Ask them. Use AskUserQuestion when the options are
     genuinely discrete, and plain prose when they are not. The plan-architect was
     told to give a default for each, so offer the default as a choice — but do
     not pick it for the user.
   - **The stages themselves.** Ask whether the stage list is right: stages to
     add, remove, merge, split, reword, or reorder. Say plainly that they can
     change any of it.

   Do not skip this step because the plan looks good to you. Confirming the plan is
   the point of the `plan` subcommand.

3. **Iterate.** Send the user's answers and edits back to
   `staged-build:plan-architect` for another proposal pass, quoted verbatim.
   Show the revised proposal. Repeat until the user is satisfied. There is no
   iteration limit — this is cheap, and it is much cheaper than discovering the
   plan was wrong at stage 04.

4. **Write.** Once the user approves, delegate to `staged-build:plan-architect` for
   a **write pass**, passing the goal, every answer, and every edit verbatim. It
   writes `SPEC.md` and `STATE.md`.

5. Show the final stage list and **stop.** Do not start implementing. No files
   under `stages/` exist yet — those are written when each stage begins.

## `next`

Take **one** stage from pending to done. One. Never begin a second.

1. **Pick the stage.** The first row in STATE.md that is not `done`. If it is
   `blocked`, stop and explain why it is blocked instead of running it.

2. **Start a change.**
   - git repo → confirm there are no uncommitted *code* changes with
     `git status --porcelain -- ':!specs'`. Changes under `specs/` are expected —
     that is the plan itself — so exclude them rather than blocking on them. If
     anything else is dirty, stop and say so. Then get on the feature branch
     exactly as **Branch policy** above describes. Reuse it; the branch is per
     feature, not per stage.
   - jj repo → `jj new -m "stage NN: <title>"`
   - neither → ask the user whether to proceed with no version control. Do not
     assume.

3. **Mark `in-progress`** for that row in STATE.md.

4. **Specify the stage.** Delegate to `staged-build:stage-architect`, passing the
   full text of `SPEC.md` and the STATE.md row for this stage. It writes
   `NN-slug.md` (the contract) and `NN-slug.detail.md` (the implementation plan),
   and reports which steps it marked `[large]`.

   If it **split** the stage into sub-stages, stop and show the new stage list —
   the plan changed and the user should see it before work starts. If it reports
   that the stage conflicts with the codebase, stop and surface that too.

5. **Verify the plan.** Nothing gets implemented until the plan has been checked
   against the real codebase. Delegate to `staged-build:plan-checker` **once**, with
   the full text of `NN-slug.md` and `NN-slug.detail.md`. Read its final line for
   `VERDICT: PASS` or `VERDICT: FAIL`.

   One check per stage. There is no per-step check — do not invoke the plan-checker
   again for `[large]` steps or for any part of the plan on its own.

   This step is not optional and not conditional on how good the plan looks to you.
   You do not read the code to judge a plan — that is exactly the substitution the
   pipeline forbids.

6. **On a plan-check FAIL** → delegate to `staged-build:stage-architect` for a
   **revision pass**, passing the checker's findings quoted verbatim alongside the
   current text of both plan files. Then re-run the check against the revised plan.
   **At most 1 revise→check cycle.** If the second check also fails, stop and report
   every finding from both attempts. Do not implement a plan that failed its check.

   If the plan-checker returns **Questions**, ask the user — you are the subagents'
   only channel — and pass the answers verbatim to the stage-architect on the
   revision pass. Questions alone do not fail a plan; do not treat them as a
   blocker unless the answer would change the plan.

7. **Show the checked plan.** Before implementation, show the user: the acceptance
   criteria, the verification command, the titles of any `[large]` steps, and the
   plan-check verdict. Then continue — this is a report, not an approval gate,
   unless the user asked to approve the plan.

8. **Implement.** Delegate to `staged-build:implementer` with the full text of both
   `NN-slug.md` and `NN-slug.detail.md`. If it stops rather than implementing, stop
   the pipeline and report what it said. Do not implement it yourself and do not
   re-prompt it to try harder.

9. **Review.** Delegate to `staged-build:reviewer` with the full text of
   `NN-slug.md` and the complete diff (`jj diff` or `git diff`, plus untracked
   files). Read its final line for `VERDICT: PASS` or `VERDICT: FAIL`.

10. **On review FAIL** → delegate to `staged-build:debugger` with `NN-slug.md` and
    the reviewer's findings quoted verbatim, then send the new diff back to
    `staged-build:reviewer`. **At most 2 debug→review cycles.** If it still fails,
    stop and report every finding across all attempts.

11. **Validate.** Delegate to `staged-build:validator` with the full text of
    `NN-slug.md` **and nothing else.**

    Do not pass `NN-slug.detail.md`. Do not pass the implementer's report. Do not
    pass the reviewer's verdict or any plan-check verdict. Do not tell it the code
    is believed to work, that review passed, that the plan was checked, or what was
    built. The validator's job is to test the assumptions everyone else already
    made, and it cannot do that if you hand it their conclusions. This independence
    is the entire reason the role exists — preserve it even though repeating
    context would be more efficient.

12. **On validation FAIL** → delegate to `staged-build:debugger` with `NN-slug.md`
    and the validator's findings verbatim, then re-run `staged-build:validator` with
    the contract alone, exactly as in step 11. **At most 2 debug→validate cycles.**
    If it still fails, stop and report.

13. **If the debugger returns `REPLANNED`** at any point → stop immediately. Show
    what changed in the stage file and why. The stage is `blocked` in STATE.md.
    Do not continue, do not re-run, do not start the next stage.

14. **On validation PASS:**
    - Write `specs/<feature>/stages/NN-slug.report.md` containing: what was built,
      files changed, each acceptance criterion with the validator's evidence, the
      plan-check findings that were fixed before implementation, the review findings
      that were accepted as non-blocking, and any debug cycles run.
    - Mark the row `done` in STATE.md.
    - Describe the commit the user should make **on the feature branch** (jj: the
      change is already there, suggest a `jj describe` message; git: the suggested
      `git commit` message). Do not commit for them unless they ask, and do not
      branch, merge, rebase, or push.
    - **Stop. Never auto-continue to the next stage.** The user runs `next` again
      when they are ready. Say which stage is next and stop.

## `status`

Show the STATE.md table above, then the most recent `*-report.md` under
`specs/<feature>/stages/`. Say which stage `next` would run, and which branch it
would run on — the `Branch:` line from STATE.md, plus a note if that branch does
not exist or is not the one currently checked out. Change nothing.

## `redo`

1. Identify the `in-progress` stage (or the last `done` one if the user means that
   — ask if it is ambiguous).
2. Abandon its change. **Ask before destroying work** — show what will be discarded
   and get a yes.
   - jj → `jj abandon`.
   - git → stay on the feature branch and reset the stage's work off it:
     `git reset --hard HEAD` if the stage was never committed, otherwise
     `git reset --hard <the commit before this stage's first commit>`. Name the
     exact ref you are about to reset to when you ask.

     **Do not delete the branch and do not create a replacement.** The branch
     carries every earlier stage of the feature; deleting it throws away work that
     `redo` was never asked to touch. The `Branch:` line in STATE.md stays as it is.
3. Set the row back to `pending` in STATE.md and delete its `.report.md`.
4. Run `next`.

---

# Reporting

Use TodoWrite to track the steps of `next` so the user can see where the pipeline is.

Report verdicts as the subagents gave them. Never upgrade a FAIL to a pass, never
soften a Critical finding, and never claim a stage is done when the validator did
not say so. If the pipeline stopped early, say exactly which step it stopped at and
why.
