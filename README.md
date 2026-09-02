# staged-build

A Claude Code plugin that runs development as a staged pipeline, with a different
model in each role.

A plan is broken into stages that can each ship on their own. Every stage is
implemented, reviewed, and then **independently validated** before the next one
starts. `/staged-build:stage next` runs exactly one stage per invocation and stops;
`/staged-build:yolo` runs the whole plan end to end and stops only for decisions
that are genuinely yours to make.

## Roles

| Role | Model | Tools | Does |
|---|---|---|---|
| `plan-architect` | opus | Read, Grep, Glob, Write, Bash | Designs the plan and proposes the stages. Asks rather than assumes. No code. |
| `stage-architect` | opus | Read, Grep, Glob, Write, Bash | Turns one stage into a precise, implementable spec. Splits it if too large. No code. |
| `plan-checker` | opus | Read, Grep, Glob, Bash | Checks a stage's plan against the real codebase before anything is built. No Write/Edit. |
| `implementer` | sonnet | Read, Write, Edit, Bash, Grep, Glob | Implements exactly one stage. |
| `reviewer` | sonnet | Read, Grep, Glob, Bash | Reads the diff against the stage file. No Write/Edit. |
| `validator` | sonnet | Read, Bash, Grep, Glob | Runs the thing and observes it. Never fixes. |
| `debugger` | sonnet | Read, Edit, Bash, Grep, Glob | Root cause, minimal fix, or replans a wrong stage. |

**Planning happens in two passes, at two altitudes.** The plan-architect works out
*what* to build and *in what order*, and settles it with you before anything is
written. The stage-architect then works out *how*, one stage at a time, so a
stage's detailed spec is written after the stages before it have actually landed —
informed by what the code really became rather than by what the plan predicted.

**No plan is implemented until it has been checked against the codebase.** Once the
stage-architect has written a stage's implementation plan, the `plan-checker` reads
the repository — and runs things against it — looking for the defects that a
passing test suite would never surface: behaviour the contract asks for that the
plan can't deliver, a secret that reaches a log or an assertion message, a
documented idiom that silently does nothing, an irreversible action nobody
authorized. It deliberately does *not* verify every citation or build a coverage
map; a wrong regex or a bad path is cheaper to find by running the code. One check
per stage, and a plan that fails goes back to the stage-architect for a single
revision pass.

The plan itself is capped: a stage's `detail.md` stays under ~300 lines and never
contains a complete implementation of any file. A spec longer than the diff it
describes is a defect — it gets planned, checked, and argued over, and then thrown
away when the implementer writes the real thing.

The **validator is given only the stage file** — never the implementer's report or
the reviewer's verdict. It re-derives whether the stage works from observed
behavior rather than inheriting anyone's conclusion. That independence is the
point of the role; the orchestrator is instructed not to "help" by passing along
context that would compromise it.

## Install

```bash
git clone <this-repo>
```

Then, in Claude Code:

```
/plugin marketplace add ./staged-build
/plugin install staged-build@staged-build
```

Or from a checkout, without installing:

```bash
claude --plugin-dir ./staged-build
```

Verify it loaded:

```bash
claude plugin details staged-build
```

You should see 7 agents and 2 commands.

### Developing on it

An installed plugin is a **cached copy**, and Claude Code only refreshes it when
`version` in `plugin.json` changes. Editing files in your checkout does nothing to
the installed copy, and `claude plugin update` reports "not found" because the
version it has is still current. You get a stale plugin with no warning — an agent
you added simply isn't there.

While iterating, skip installation and load the checkout directly:

```bash
claude --plugin-dir ./staged-build
```

That takes precedence over an installed plugin of the same name for the session.
Use `/reload-plugins` to pick up edits without restarting. When you do want to
publish a change, bump `version` in **both** `.claude-plugin/plugin.json` and the
matching entry in `.claude-plugin/marketplace.json`, or reinstall:

```bash
claude plugin uninstall staged-build && claude plugin install staged-build@staged-build
```

### Names after install

Everything is namespaced by the plugin. The commands are
**`/staged-build:stage`** and **`/staged-build:yolo`**, not `/stage` and `/yolo`.
The agents are `staged-build:plan-architect`,
`staged-build:stage-architect`, `staged-build:plan-checker`,
`staged-build:implementer`, `staged-build:reviewer`, `staged-build:validator`,
`staged-build:debugger` — the names you see in `@`-mention typeahead, and the names
the orchestrator delegates to.

## Usage

### `/staged-build:stage plan "<goal>"`

Planning is a conversation, not a single shot. The plan-architect reads the repo,
analyses the goal for feasibility, and comes back with a **proposal** — no files
written yet. You then:

- **Answer its questions.** It is instructed to ask rather than assume, and to give
  a sensible default for each question so you can wave through the ones you don't
  care about.
- **Change the stages.** Add, remove, merge, split, reword, or reorder any of them.
  It follows your edits rather than re-arguing them.

It re-proposes until you're happy, and only then writes `SPEC.md` and `STATE.md`.
No code is written, and no stage specs exist yet.

Anything the plan-architect cannot confirm is feasible becomes **stage 01** — a
short investigation stage that answers the question and writes down the finding, so
the rest of the plan isn't built on a guess.

Subagents can't prompt you directly (Claude Code removes `AskUserQuestion` from
every subagent), so the orchestrator relays the questions and your answers in both
directions.

### `/staged-build:stage next`

Takes **one** stage from pending to done:

1. Gets on the feature's branch (`jj new`, or the one `git` branch for this feature
   — see [Branches](#branches))
2. Marks the stage `in-progress`
3. **stage-architect** writes the stage's acceptance contract and implementation plan
4. **plan-checker** checks that plan against the codebase → `VERDICT: PASS` / `FAIL`
   (once per stage — there is no per-step check)
5. On FAIL → **stage-architect** revises the plan, then re-check (max 1 cycle).
   Nothing is implemented until the plan passes.
6. **implementer** implements it
7. **reviewer** reviews the diff, happy path and failure paths → `VERDICT: PASS` / `FAIL`
8. On FAIL → **debugger**, then re-review (max 2 cycles)
9. **validator** independently verifies, given only the stage file
10. On FAIL → **debugger**, then re-validate (max 2 cycles)
11. On PASS → writes a stage report, marks it `done`, describes the commit, **stops**

It never auto-continues. Run `next` again for the following stage.

If the debugger concludes the *stage spec* was wrong rather than the code, it
updates the stage file, marks the stage `blocked`, and reports `REPLANNED` — the
pipeline stops immediately so you can look at the plan.

#### Branches

**One branch for the whole feature.** The first `next` creates
`staged-build/<feature>` and records it on the `Branch:` line of
`specs/<feature>/STATE.md`; every stage after that checks out the same branch and
commits onto it. There is no branch per stage.

The orchestrator will only create a second branch if you explicitly ask —
`/staged-build:stage next --new-branch`, or just saying so. It then records the new
branch in STATE.md and every following stage uses that one. It never deletes a
branch, including in `redo`, and it never merges, rebases, or pushes.

If the recorded branch has gone missing, `next` stops and asks rather than quietly
branching from wherever HEAD happens to be.

In a **jj** repo this doesn't apply: `jj new` starts a change, not a branch, so each
stage still starts its own change and bookmarks are left alone.

### `/staged-build:stage status`

The stage table plus the most recent stage report. Changes nothing.

### `/staged-build:stage redo`

Resets the current stage to `pending`, abandons its change, and runs `next` again.
It asks before destroying work, naming the exact ref it would reset to. It stays on
the feature branch — the branch holds every earlier stage, so `redo` resets the
current stage's work off it rather than deleting it.

### `/staged-build:yolo`

Runs **every** pending stage, back to back, committing as it goes. Same pipeline
per stage — stage-architect, plan-checker, implementer, reviewer, validator — with
three differences.

**It decides small things itself.** Every judgement call a subagent hands back is
triaged as major or minor. Minor is naming, placement, a default value, message
wording, which of two equivalent helpers to call — it decides, writes it down, and
carries on. Major stops the run and asks you: anything that changes scope or
acceptance criteria, anything not cheaply reversible (data model, schema,
migration, on-disk or wire format, public API, new dependency), anything touching
auth, credentials, billing, or user data, anything destructive or outward-facing,
and any convention you haven't set. The pipeline's own stop conditions — the
implementer stopping, a `REPLANNED`, a check that still fails after its cycles —
are always major.

If it's still weighing which one a call is, it treats it as major. A wrong minor
decision costs an edit; a wrong major one costs every stage built on top of it.

**Every minor decision stays changeable.** It has to land as one named place to
change — a named constant, a default parameter, a single config key — and the log
records that `file:line`. One named place, not an abstraction layer: over-building
a small decision is itself a decision you didn't ask for.

**You review the decisions at the end.** They accumulate in
`specs/<feature>/DECISIONS.md`, and when the last stage passes, `yolo` adds one
more stage to `STATE.md` — *Confirm autonomous decisions* — and walks you through
them. Anything you don't pick is confirmed. Anything you want changed goes back to
the plan-architect as appended stages, and the run continues through those.

```
/staged-build:yolo                 # run the existing plan to completion
/staged-build:yolo "<goal>"        # plan it first, then run it
```

Given a goal with no plan, it runs the normal planning conversation first. That
gate isn't skipped: approving the plan is the most major decision there is.

#### Branches and commits under yolo

**One branch, and it commits.** If `STATE.md` already names a branch it uses that.
Otherwise it adopts the branch you're on when that branch is a *clean new branch* —
not the default branch, nothing dirty outside `specs/`, no commits of its own — and
creates `staged-build/<feature>` when it isn't. Either way it records the name and
tells you which happened.

Then one commit per stage, and one per `[large]` step within a stage. It never
creates a second branch, never deletes one, and never merges, rebases, or pushes —
pushing is outward-facing, so it's outside what `yolo` authorizes even when the
branch has a remote. If the run stops on a major issue, the tree is left exactly as
it is rather than tidied up.

#### What the judges don't see

The reviewer, validator, and plan-checker never receive the decision log or the
autonomy policy. Telling a judge which departures were pre-approved is how it stops
being one. The validator in particular still gets the stage file and nothing else —
unattended running makes that the last independent check in the pipeline, not a
formality to trim. If the reviewer flags a logged decision, that's a real finding
and goes to the debugger like any other.

## State layout

Plan state lives in `specs/`, in your project, one directory per feature:

```
specs/<feature>/
  SPEC.md                    # goal, context, approach, stages, non-goals
  STATE.md                   # the stage table and each stage's status
  stages/
    01-slug.md               # acceptance contract: criteria, verification, out-of-scope
    01-slug.detail.md        # implementation plan: signatures, edge cases, test plan
    01-slug.report.md        # written when the stage passes validation
  DECISIONS.md               # yolo only: decisions taken without asking
```

`STATE.md` also carries the feature's `Branch:` line — the one branch every stage
is built on.

`DECISIONS.md` is written only by `/staged-build:yolo`, and only by the
orchestrator — subagents report their decisions and it transcribes them, so the
format stays uniform and the log is complete even if the run stops mid-stage. Each
entry carries the call that was open, what was chosen and over what, why, the
`file:line` that holds it, and a status of `unconfirmed`, `confirmed`, or
`changed → stage NN`.

`SPEC.md` and `STATE.md` are written by the plan-architect up front. Everything
under `stages/` is written by the stage-architect when that stage begins — so a
stage spec reflects the code as it actually exists by then, not as the plan
predicted it would.

The split between `NN-slug.md` and `NN-slug.detail.md` is deliberate: the contract
holds only externally checkable behavior, and it is the single file the validator
receives. The implementation plan never reaches it, so the validator cannot end up
checking the code against the implementer's own intentions.

This is per-project working state. It is never bundled with the plugin. Commit it
or don't — the pipeline ignores `specs/` when it checks whether your tree is clean,
so an uncommitted plan won't block `next`.

> **Why `specs/` and not `.claude/plan/`:** Claude Code treats everything under
> `.claude/` as a sensitive path. Writes there are refused even with an explicit
> `Edit(.claude/plan/**)` allow rule and `--permission-mode acceptEdits`, and they
> hard-fail in non-interactive runs. Reads are fine, which is why
> `.claude/pipeline.json` still works — the pipeline only ever reads it.

## Model routing

Defaults live in `pipeline.json` at the plugin root:

```json
{
  "plan-architect": "opus",
  "stage-architect": "opus",
  "plan-checker": "opus",
  "implementer": "sonnet",
  "reviewer": "sonnet",
  "validator": "sonnet",
  "debugger": "sonnet"
}
```

**To change routing for a project, create `.claude/pipeline.json`** in that
project. It replaces the plugin default. No agent files need editing:

```json
{
  "plan-architect": "opus",
  "stage-architect": "opus",
  "plan-checker": "opus",
  "implementer": "haiku",
  "reviewer": "opus",
  "validator": "sonnet",
  "debugger": "opus"
}
```

Resolution order, highest first:

1. `.claude/pipeline.json` in the project
2. `pipeline.json` bundled with the plugin
3. The `model:` field in each agent's own frontmatter — a last-resort default so
   the agents still work if both configs are missing

Values are model aliases: `opus`, `sonnet`, `haiku`, `fable`.

A role may name an *agent* instead of a model in a future version — see
[docs/gemini-optional.md](docs/gemini-optional.md). That path is designed but not
built; the object form is ignored today.

## Two environment variables that will silently break this

### `CLAUDE_CODE_SUBAGENT_MODEL` overrides everything

> "Accepts an alias such as `haiku` or a full model name, and **overrides the
> per-invocation `model` parameter and the subagent definition's `model`
> frontmatter**."

If this is set in your environment or settings, **every role runs on that one
model** and all routing in this plugin is dead — silently. The pipeline will still
appear to work; you just lose the multi-model separation that is the entire point.

Check for it before you trust a run:

```bash
echo "${CLAUDE_CODE_SUBAGENT_MODEL:-(unset — good)}"
```

Set it to `inherit` to restore normal resolution.

### `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` is required for interactive use

In interactive sessions, subagents default to running in the **background**, and
their results arrive a turn later. The pipeline is strictly sequential — the
reviewer needs the implementer's diff, the debugger needs the reviewer's findings
— so backgrounding breaks it: steps fire out of order, or against a diff that does
not exist yet.

Add this to `~/.claude/settings.json` (or the project's `.claude/settings.json`):

```json
{
  "env": {
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"
  }
}
```

Non-interactive runs (`claude -p`) already wait for subagents, so this only
matters interactively.

## Verifying that routing actually worked

Don't take the pipeline's word for it. Each subagent writes its own transcript,
which records the model it really ran on:

```bash
# after a run, from the project directory
# note: pwd -P, not pwd — the path must be the physical one
D=~/.claude/projects/$(pwd -P | sed 's|/|-|g')
for f in $(find "$D" -path '*subagents*' -name '*.jsonl'); do
  printf '%s  %s\n' \
    "$(grep -o '"agentType":"[^"]*"' "${f%.jsonl}.meta.json")" \
    "$(grep -o '"model":"[^"]*"' "$f" | sort -u | tr '\n' ' ')"
done
```

Example output from a real run of this pipeline:

```
"agentType":"staged-build:plan-architect"   "model":"claude-opus-5"
"agentType":"staged-build:stage-architect"  "model":"claude-opus-5"
"agentType":"staged-build:implementer"      "model":"claude-sonnet-5"
"agentType":"staged-build:reviewer"         "model":"claude-opus-5"
"agentType":"staged-build:validator"        "model":"claude-sonnet-5"
"agentType":"staged-build:debugger"         "model":"claude-opus-5"
```

Each line pairs the scoped agent name with the model it used. If every line shows
the same model, check `CLAUDE_CODE_SUBAGENT_MODEL`.

## Requirements and limits

- No system-level permissions and no external binaries.
- Writing files under `specs/` needs the normal edit permission you'd grant any
  Claude Code session.
- **The validator and debugger must be able to run your test command.** They are
  the two roles whose whole job is executing the code rather than reading it. If
  your project's runner isn't permitted, both stop and report the denial — which
  is correct behavior, but it halts the stage. Allow the runner in your project's
  `.claude/settings.local.json`:

  ```json
  {
    "permissions": {
      "allow": ["Bash(python3:*)", "Bash(npm test:*)", "Bash(cargo test:*)"]
    }
  }
  ```

  Use whatever your stage's **Verification** command actually invokes. The
  debugger will not work around a denial — it will not disable the sandbox, and
  it will not "fix" a failure it was never able to reproduce.
- **The plan-checker never modifies your tree.** It has no Write and no Edit. It
  may run things — a snippet from the plan, a glob, a scratch reproduction in
  `/tmp` — because a finding it demonstrated beats one it reasoned about, but it
  makes no change inside the repository and runs no build that leaves artifacts
  there.
- Plugin agents ignore `hooks`, `mcpServers`, and `permissionMode` frontmatter —
  this plugin does not use those fields.
- Stages are sized for roughly 400 lines of diff, and steps within a stage are
  marked `[large]` at roughly 150. A stage much larger than that is a planning bug;
  the stage-architect splits it when `next` reaches it, and the pipeline stops so
  you can see the new breakdown before any code is written. The `[large]` mark now
  only tells `yolo` where to put a per-step commit; it no longer buys the step a
  check of its own.
- Plan checking costs one extra subagent run per stage, and at most one more if the
  plan fails and is revised. It is scoped to what a test run can't tell you; a plan
  that fails twice stops the pipeline rather than going round again.
- **`/staged-build:yolo` needs `Bash(git commit:*)` and `Bash(git add:*)`**, since
  it commits each stage rather than describing the commit for you. Without them the
  run stops on the first permission prompt, which defeats the point of running
  unattended. It never needs push, merge, rebase, or branch deletion, so there is no
  reason to allow those.

## License

MIT
