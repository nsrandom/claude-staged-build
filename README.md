# staged-build

A Claude Code plugin that runs development as a staged pipeline, with a different
model in each role.

A plan is broken into stages that can each ship on their own. Every stage is
implemented, reviewed, and then **independently validated** before the next one
starts. The pipeline runs exactly one stage per invocation and stops.

## Roles

| Role | Model | Tools | Does |
|---|---|---|---|
| `plan-architect` | opus | Read, Grep, Glob, Write, Bash | Designs the plan and proposes the stages. Asks rather than assumes. No code. |
| `stage-architect` | opus | Read, Grep, Glob, Write, Bash | Turns one stage into a precise, implementable spec. Splits it if too large. No code. |
| `implementer` | sonnet | Read, Write, Edit, Bash, Grep, Glob | Implements exactly one stage. |
| `reviewer` | opus | Read, Grep, Glob, Bash | Reads the diff against the stage file. No Write/Edit. |
| `validator` | sonnet | Read, Bash, Grep, Glob | Runs the thing and observes it. Never fixes. |
| `debugger` | opus | Read, Edit, Bash, Grep, Glob | Root cause, minimal fix, or replans a wrong stage. |

**Planning happens in two passes, at two altitudes.** The plan-architect works out
*what* to build and *in what order*, and settles it with you before anything is
written. The stage-architect then works out *how*, one stage at a time, so a
stage's detailed spec is written after the stages before it have actually landed —
informed by what the code really became rather than by what the plan predicted.

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

You should see 5 agents and 1 skill.

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

Everything is namespaced by the plugin. The command is **`/staged-build:stage`**,
not `/stage`. The agents are `staged-build:plan-architect`,
`staged-build:stage-architect`, `staged-build:implementer`,
`staged-build:reviewer`, `staged-build:validator`, `staged-build:debugger` — the
names you see in `@`-mention typeahead, and the names the orchestrator delegates to.

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

1. Starts a change (`jj new`, or a `git` branch — detected, not assumed)
2. Marks the stage `in-progress`
3. **stage-architect** writes the stage's acceptance contract and implementation plan
4. **implementer** implements it
5. **reviewer** reviews the diff → `VERDICT: PASS` / `FAIL`
6. On FAIL → **debugger**, then re-review (max 2 cycles)
7. **validator** independently verifies, given only the stage file
8. On FAIL → **debugger**, then re-validate (max 2 cycles)
9. On PASS → writes a stage report, marks it `done`, describes the commit, **stops**

It never auto-continues. Run `next` again for the following stage.

If the debugger concludes the *stage spec* was wrong rather than the code, it
updates the stage file, marks the stage `blocked`, and reports `REPLANNED` — the
pipeline stops immediately so you can look at the plan.

### `/staged-build:stage status`

The stage table plus the most recent stage report. Changes nothing.

### `/staged-build:stage redo`

Resets the current stage to `pending`, abandons its change, and runs `next` again.
It asks before destroying work.

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
```

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
  "implementer": "sonnet",
  "reviewer": "opus",
  "validator": "sonnet",
  "debugger": "opus"
}
```

**To change routing for a project, create `.claude/pipeline.json`** in that
project. It replaces the plugin default. No agent files need editing:

```json
{
  "plan-architect": "opus",
  "stage-architect": "opus",
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
- Plugin agents ignore `hooks`, `mcpServers`, and `permissionMode` frontmatter —
  this plugin does not use those fields.
- Stages are sized for roughly 400 lines of diff. A stage much larger than that
  is a planning bug; the stage-architect splits it when `next` reaches it, and the
  pipeline stops so you can see the new breakdown before any code is written.

## License

MIT
