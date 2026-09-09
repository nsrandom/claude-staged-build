# staged-build

A Claude Code plugin that runs development as a staged pipeline, with a different
model in each role.

A plan is broken into stages that can each ship on their own. Every stage is
specified, implemented, and then **independently verified** — read against its
contract *and* actually run — before the next one starts.
`/staged-build:stage next` runs exactly one stage per invocation and stops;
`/staged-build:yolo` runs the whole plan end to end and stops only for decisions
that are genuinely yours to make.

## Roles

| Role | Model | Tools | Does |
|---|---|---|---|
| `plan-architect` | opus | Read, Grep, Glob, Write, Edit, Bash | Designs the plan and writes it straight to `specs/<feature>/`. Asks rather than assumes. No code. |
| `stage-runner` | sonnet | Read, Write, Edit, Bash, Grep, Glob, Agent | Ephemeral sub-orchestrator. Runs one stage's whole lifecycle in an isolated context and returns ≤800 tokens. |
| `stage-architect` | opus | Read, Grep, Glob, Write, Edit, Bash | Turns one stage into a precise, implementable spec on disk. Splits it if too large. No code. |
| `implementer` | sonnet | Read, Write, Edit, Bash, Grep, Glob | Implements exactly one stage, and self-heals when verification fails. |
| `verifier` | sonnet | Read, Grep, Glob, Bash | Reads the diff against the contract *and* runs the thing. Never fixes. |

Five roles, and the structural ideas that make them work:

**The root orchestrator stays small.** It talks to you, owns the plan files, and
delegates each stage whole to an ephemeral `stage-runner`. That runner drives
`stage-architect`, `implementer`, and `verifier`, commits, writes the stage report
to disk, and dies — handing back a summary of at most 800 tokens. Test
transcripts, diffs, and fix cycles never reach your conversation. That is what lets
a twenty-stage `yolo` run reach stage 20 instead of drowning in its own history. In
`yolo` it goes further and resets between stages, reloading only `STATE.md`,
`DECISIONS.md`, `SESSION_STATE.json`, and the previous report summaries, so a long
run does not accumulate its own history quadratically.

**Subagents write evidence to disk, not into the chat.** Every role has a return
budget — `implementer` and `stage-architect` ≤350 tokens, `verifier` ≤300,
`stage-runner` ≤800 — and the exhaustive material goes to
`specs/<feature>/stages/`: line-by-line changes and decision rationale to
`*.report.md`, per-file plans to `*.detail.md`, and raw command and test output to
`*.verification.log`. Return payloads carry decision *IDs*, not decision write-ups.
`/staged-build:analyze-tokens` audits, per role, whether they actually did.

**A stuck implementer relays instead of thrashing.** An implementer has 25 turns.
Its own transcript is re-read on every one of them, so ten rounds of edit-run-read
pay for the first test log ten times. At turn 20 it audits itself, and if it is
looping it writes `NN-slug.wip.md` — what passes, what is stuck, what it has ruled
out — and returns `RELAY_REQUIRED`. The runner then starts a *fresh* implementer
holding only the contract, that checkpoint, and the workspace on disk: a few
thousand tokens instead of a hundred thousand, and usually the thing that gets it
unstuck. Two relays per stage; a third means the stage is wrong, not the agent.

**Planning happens at two altitudes.** The plan-architect works out *what* to build
and *in what order*, and settles it with you. The stage-architect then works out
*how*, one stage at a time — so a stage's spec is written after the stages before
it have actually landed, informed by what the code really became rather than by
what the plan predicted. For stage N > 1 it is given the Summary and Changes
sections of every prior stage report as ground truth, which is what stops stage 05
from being specified against an interface stage 03 never built.

**A finished feature leaves one page, not twenty.** At the end of
`/staged-build:cleanup`, the feature gets `specs/<feature>/architecture.md`:
entrypoints, module map, public API and CLI contracts, invariants, config keys with
their `file:line`, and the test command. It is synthesised from `DECISIONS.md`, the
stage reports' Summary and Changes sections, and `SESSION_STATE.json` — never by
re-reading the source, which would make the documentation step the token sink it
exists to prevent. Later features and maintenance sessions read that one file
instead of grepping the tree: `SPEC.md` predates the code and has drifted, and the
stage reports are the truth in fifteen fragments.

**The verifier is given the contract and the diff, and nothing else** — never the
implementation plan, the implementer's report, or the decision log. It re-derives
whether the stage works from what it reads and what it observes, rather than
inheriting anyone's conclusion. That independence is the point of the role, and the
orchestrator is instructed not to "help" by passing along context that would
compromise it.

The plan itself is capped: a stage's `detail.md` stays under ~300 lines and never
contains a complete implementation of any file. A spec longer than the diff it
describes is a defect — it gets planned and argued over, then thrown away when the
implementer writes the real thing.

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

You should see 5 agents and 4 commands.

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
**`/staged-build:stage`**, **`/staged-build:yolo`**, **`/staged-build:cleanup`**,
and **`/staged-build:analyze-tokens`** — not `/stage` and `/yolo`. The agents are
`staged-build:plan-architect`, `staged-build:stage-runner`,
`staged-build:stage-architect`, `staged-build:implementer`, and
`staged-build:verifier` — the names you see in `@`-mention typeahead, and the names
the orchestrator delegates to.

## Usage

### `/staged-build:stage plan "<goal>"`

Planning is a conversation, not a single shot — but it is grounded in a real file
from the first pass. The plan-architect reads the repo, analyses the goal for
feasibility, and **writes the plan straight to `specs/<feature>/`**: `SPEC.md`,
`STATE.md`, an empty `DECISIONS.md`, and a `SESSION_STATE.json`. You then read the
actual files in your editor, and:

- **Answer its questions.** It is instructed to ask rather than assume, and to give
  a sensible default for each so you can wave through the ones you don't care about.
- **Change the stages.** Add, remove, merge, split, reword, or reorder any of them.
  It edits the files in place rather than re-arguing them.

It iterates until you're happy. No code is written, and no stage specs exist yet.

Anything the plan-architect cannot confirm is feasible becomes **stage 01** — a
short investigation stage that answers the question and writes down the finding, so
the rest of the plan isn't built on a guess.

Subagents can't prompt you directly (Claude Code removes `AskUserQuestion` from
every subagent), so the orchestrator relays questions and answers in both
directions.

### `/staged-build:stage next`

Takes **one** stage from pending to done:

1. Gets on the feature's branch (`jj new`, or the one `git` branch for this feature
   — see [Branches](#branches))
2. Marks the stage `in-progress`
3. **stage-architect** writes the acceptance contract and the implementation plan
   to disk, and returns a ≤350-token summary
4. **You verify the plan.** The orchestrator shows the criteria, the verification
   command, the files in scope, and the test plan, and asks before anything is
   built. Want changes? The stage-architect revises the files in place.
5. **stage-runner** takes it from there in an isolated context:
   **implementer** implements → **verifier** reads the diff *and* runs it →
   `VERDICT: PASS` / `FAIL`
6. On FAIL → the findings go straight back to the **implementer** to self-heal,
   then re-verify (max 2 cycles). If the implementer instead hits its 25-turn
   budget, it checkpoints and the runner relays the stage to a fresh one (max 2
   relays) — all of which happens inside the runner and reaches you as a count
7. On PASS → commits the stage, writes `NN-slug.report.md`, and returns a short
   summary. The orchestrator marks it `done` and **stops**

It never auto-continues. Run `next` again for the following stage.

If the implementer concludes the *stage spec* was wrong rather than the code, it
returns `REPLANNED` — the stage is marked `blocked` and the pipeline stops
immediately so you can look at the plan.

Minor decisions taken along the way are logged to `DECISIONS.md` in `next` too, not
just in `yolo`. That keeps the flow moving without stopping on a variable name, and
leaves the audit trail for `/staged-build:cleanup`.

#### Branches

**One branch per feature, never `main`.** The first `next` creates a branch named
after the feature — just `<feature>`, no prefix — and records it on the `Branch:`
line of `specs/<feature>/STATE.md`; every stage after that checks out the same
branch and commits onto it. There is no branch per stage. If a branch of that name
already exists, it asks whether you want to reuse it rather than checking it out on
its own.

Commits on the branch are prefixed `<feature>-stage-NN: `, so stage 03 of
`search-index` commits as `search-index-stage-03: add the tokenizer`. In `next` the
`stage-runner` makes that commit once verification passes.

The orchestrator will only create a second branch if you explicitly ask —
`/staged-build:stage next --new-branch`, or just saying so. It never deletes a
branch, including in `redo`, and it never merges, rebases, or pushes.

If the recorded branch has gone missing, `next` stops and asks rather than quietly
branching from wherever HEAD happens to be.

In a **jj** repo this doesn't apply: `jj new` starts a change, not a branch, so each
stage still starts its own change and bookmarks are left alone.

### `/staged-build:stage status`

The stage table, the branch, the session state, a decision summary by tier and
status, what's in the scratchpad, and the latest stage report. Changes nothing.

### `/staged-build:stage redo`

Resets the current stage to `pending`, discards its work, and runs `next` again.
It asks before destroying anything, naming the exact ref it would reset to. It
stays on the feature branch — the branch holds every earlier stage, so `redo`
resets the current stage's work off it rather than deleting it.

### `/staged-build:yolo`

Runs **every** pending stage, back to back, committing as it goes. Each stage goes
to its own `stage-runner`, so the root orchestrator collects short summaries rather
than twenty stages of build output — and it **resets between stages**, dropping the
finished stage's material and reloading only `STATE.md`, `DECISIONS.md`,
`SESSION_STATE.json`, and the previous report summaries. Everything it needs is on
disk, so nothing is lost; what goes away is the per-turn cost of re-reading stage 3
while running stage 12. If a run does grow long, it stops at a stage boundary and
tells you to `/clear` and run `/staged-build:yolo` again, which resumes from
`STATE.md` with a clean slate.

**It decides small things itself.** Every judgement call a subagent hands back is
triaged as major or minor. Minor is naming, placement, a default value, message
wording, which of two equivalent helpers to call — it decides, writes it down, and
carries on. Major stops the run and asks you: anything that changes scope or
acceptance criteria, anything not cheaply reversible (data model, schema,
migration, on-disk or wire format, public API, new dependency), anything touching
auth, credentials, billing, or user data, anything destructive or outward-facing,
and any convention you haven't set. The pipeline's own stop conditions — the
implementer stopping, a `REPLANNED`, verification still failing after 2 fix cycles
— are always major.

If it's still weighing which one a call is, it treats it as major. A wrong minor
decision costs an edit; a wrong major one costs every stage built on top of it.

**Minor decisions come in two tiers.** Tier 1 is a routine convention — a name, a
default, a log message, where a test file goes. Tier 2 shapes behaviour or a
contract — strict versus lenient validation, evaluation ordering, an addition to a
public surface, fallback behaviour when something optional is absent. Both get
decided without stopping; the tier decides how much of your attention each gets
back at cleanup.

**Every minor decision stays changeable.** It has to land as one named place to
change — a named constant, a default parameter, a single config key — and the log
records that `file:line`. One named place, not an abstraction layer: over-building
a small decision is itself a decision you didn't ask for.

```
/staged-build:yolo                 # run the existing plan to completion
/staged-build:yolo "<goal>"        # plan it first, then run it
```

Given a goal with no plan, it runs the normal planning conversation first. That
gate isn't skipped: approving the plan is the most major decision there is.

When the last stage passes it points you at `/staged-build:cleanup`.

#### Branches and commits under yolo

**One branch, and it commits.** If `STATE.md` already names a branch it uses that.
Otherwise it adopts the branch you're on when that branch is a *clean new branch* —
not the default branch, nothing dirty outside `specs/`, no commits of its own — and
creates `<feature>` when it isn't. If `<feature>` already exists, that's a major
decision: it stops and asks whether to reuse it. Either way it records the name and
tells you which happened. It never builds a stage on `main`.

Then one commit per stage, each message prefixed `<feature>-stage-NN: `. It never
creates a second branch, never deletes one, and never merges, rebases, or pushes —
pushing is outward-facing, so it's outside what `yolo` authorizes even when the
branch has a remote. If the run stops on a major issue, the tree is left exactly as
it is rather than tidied up.

#### What the judge doesn't see

The verifier never receives the decision log or the autonomy addendum. Telling a
judge which departures were pre-approved is how it stops being one. It gets the
contract and the diff — unattended running makes that the last independent check in
the pipeline, not a formality to trim. If the verifier flags a logged decision,
that's a real finding and goes back to the implementer like any other.

### `/staged-build:cleanup`

The last stage of a feature: you find out what was decided in your absence and say
whether you agree.

It **starts in a fresh context** loaded only from disk — the session state, the
spec, the stage table, the decision log, and a listing of the scratchpad. That
reset is the reason it's a separate command rather than the tail of `yolo`: a
twenty-stage run leaves a transcript that would swamp the review, and none of it is
needed to ask whether you agree with a decision.

Then:

- **Tier 1 in one batch.** Every routine decision in a single table, with one
  question: confirm all of them, or pick any you want to look at.
- **Tier 2 one at a time.** For each: the problem, what was decided and the
  `file:line` that holds it, and the tradeoffs. You can confirm, reject, defer, ask
  a follow-up, or ask for a modification.
- **A real cleanup stage.** If anything was rejected or modified, or if the
  scratchpad still exists, it appends a stage to `STATE.md` and runs it through the
  normal pipeline — stage-architect, implementer, verifier, commit. The verifier
  confirms the remediations landed, the scratchpad is gone, and the tests still
  pass.
- **`architecture.md`, last.** Once the remediation has landed and the scratchpad
  is gone, it writes `specs/<feature>/architecture.md` from the decision log, the
  stage reports, and `SESSION_STATE.json`, and commits it. That is the file every
  later feature and maintenance session should read first.

### `/staged-build:analyze-tokens`

Where the tokens, the wall-clock time, and the tool calls actually went — per role,
per stage, per subagent run. It reads Claude Code's own transcripts under
`~/.claude/projects/`, and writes `tokens_efficiency_report.md` and `.json` into
`specs/<feature>/`.

It also audits the pipeline's own invariants, and this is the part worth running:

- **`verifier-independence`** — did anything reach the verifier that shouldn't
  have? A stage whose verifier saw the implementation plan has a verdict worth less
  than it looks.
- **`return-payload`** — did each role stay inside its own budget (350 / 350 / 300
  / 800), or did the evidence leak upward instead of going to disk?
- **`turn-budget`** — did an implementer run past 25 turns instead of checkpointing
  and relaying? This is usually the single largest number in an expensive run.
- **`retry-loop` / `retry-budget`** — how many fix cycles and relays each stage
  really took.
- **`latency` / `runaway-turns`** — which agent ran long, and what tool it was
  thrashing on.

It also reports which features have an `architecture.md`, and says so when a
completed feature is missing one.

Run it whenever a stage feels slow, not only at the end; `yolo` and `next` will
suggest it themselves if a stage exceeds ~15 minutes or 2 fix cycles.

```bash
/staged-build:analyze-tokens
/staged-build:analyze-tokens --feature search-index
```

The script is plain Python with no dependencies, so you can also run it directly:

```bash
python3 scripts/analyze_tokens.py --repo . --feature search-index
```

## State layout

Plan state lives in `specs/`, in your project, one directory per feature:

```
specs/<feature>/
  architecture.md                # written by cleanup: the permanent system reference
  SPEC.md                        # goal, context, approach, stages, non-goals
  STATE.md                       # the stage table, and the feature's Branch: line
  DECISIONS.md                   # decisions taken without asking, with tiers
  SESSION_STATE.json             # test command, runtime, and other environment facts
  scratchpad/                    # one-off throwaway verification code (gitignored)
  tokens_efficiency_report.md    # written by analyze-tokens
  tokens_efficiency_report.json  # the same data, for comparing runs later
  stages/
    01-slug.md                   # acceptance contract: criteria, verification, out-of-scope
    01-slug.detail.md            # implementation plan: signatures, edge cases, test plan
    01-slug.wip.md               # relay checkpoint, if the implementer needed one
    01-slug.report.md            # evidence, and what actually landed
    01-slug.verification.log     # the verifier's raw command output, kept off every context
```

`SPEC.md`, `STATE.md`, `DECISIONS.md`, and `SESSION_STATE.json` are written by the
plan-architect up front. Everything under `stages/` is written when that stage
begins — so a stage spec reflects the code as it actually exists by then, not as
the plan predicted it would.

**`architecture.md` is the cure for the exploration tax.** It is written once, at
the end of `cleanup`, from metadata already verified on disk — the decision log,
the stage reports' Summary and Changes sections, and `SESSION_STATE.json`. About a
page: entrypoints, module map, public API and CLI contracts, invariants and data
flow, an exhaustive table of config keys and named constants with their `file:line`,
and the test command. Point the next feature — or the next maintenance session — at
that file instead of letting it rediscover the same facts across dozens of
`Read` and `Grep` calls. `/staged-build:stage`, `/staged-build:yolo`, and
`/staged-build:cleanup` all list the ones that exist, and pass the relevant one down
to their subagents.

**`SESSION_STATE.json` is the cure for environment amnesia.** The test command, the
interpreter path, the flag your suite needs — recorded once, read by every later
stage, and reloaded by `cleanup` after the context reset.

**`scratchpad/` is where throwaway code is allowed to be ugly.** When an agent needs
a probe script, a scratch database, or a harness that reaches into internals to
confirm an assumption, it goes there. The directory is gitignored, the verifier
checks that nothing of that kind leaked into `src/` or the main tests directory,
and `cleanup` deletes it. Long-term tests go in the project's real tests directory
instead — the stage-architect specifies which is which.

The split between `NN-slug.md` and `NN-slug.detail.md` is deliberate: the contract
holds only externally checkable behaviour, and it is the only stage file the
verifier receives. The implementation plan never reaches it, so the verifier cannot
end up checking the code against the implementer's own intentions.

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
  "stage-runner": "sonnet",
  "stage-architect": "opus",
  "implementer": "sonnet",
  "verifier": "sonnet"
}
```

**To change routing for a project, create `.claude/pipeline.json`** in that
project. It replaces the plugin default. No agent files need editing:

```json
{
  "plan-architect": "opus",
  "stage-runner": "sonnet",
  "stage-architect": "opus",
  "implementer": "haiku",
  "verifier": "opus"
}
```

Resolution order, highest first:

1. `.claude/pipeline.json` in the project
2. `pipeline.json` bundled with the plugin
3. The `model:` field in each agent's own frontmatter — a last-resort default so
   the agents still work if both configs are missing

Values are model aliases: `opus`, `sonnet`, `haiku`, `fable`.

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
verifier needs the implementer's diff, the implementer needs the verifier's
findings — so backgrounding breaks it: steps fire out of order, or against a diff
that does not exist yet.

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

Don't take the pipeline's word for it. `/staged-build:analyze-tokens` reports the
model each role really ran on, but you can also read it straight off the
transcripts:

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
"agentType":"staged-build:verifier"         "model":"claude-sonnet-5"
```

Each line pairs the scoped agent name with the model it used. If every line shows
the same model, check `CLAUDE_CODE_SUBAGENT_MODEL`.

## Requirements and limits

- No system-level permissions and no external binaries. `analyze-tokens` needs
  `python3`, which macOS and every Linux distribution already have.
- Writing files under `specs/` needs the normal edit permission you'd grant any
  Claude Code session.
- **The verifier and implementer must be able to run your test command.** They are
  the two roles whose job involves executing the code rather than reading it. If
  your project's runner isn't permitted, they stop and report the denial — which is
  correct behaviour, but it halts the stage. Allow the runner in your project's
  `.claude/settings.local.json`:

  ```json
  {
    "permissions": {
      "allow": ["Bash(python3:*)", "Bash(npm test:*)", "Bash(cargo test:*)"]
    }
  }
  ```

  Use whatever your stage's **Verification** command actually invokes. The verifier
  will not work around a denial — it will not disable the sandbox, and it will not
  report a pass it never observed.
- **The verifier never modifies your tree.** It has no Write and no Edit. It runs
  things — the verification command, your test suite, the CLI it is checking —
  because a finding it demonstrated beats one it reasoned about, but it makes no
  change inside the repository.
- **`stage-runner` needs the Agent tool** to spawn its three subagents. If nested
  delegation is unavailable in your setup, it halts and says so rather than quietly
  doing the work itself, and the root orchestrator runs the stage directly.
- Plugin agents ignore `hooks`, `mcpServers`, and `permissionMode` frontmatter —
  this plugin does not use those fields.
- Stages are sized for roughly 400 lines of diff, and steps within a stage are
  marked `[large]` at roughly 150. A stage much larger than that is a planning bug;
  the stage-architect splits it when `next` reaches it, and the pipeline stops so
  you can see the new breakdown before any code is written.
- **`/staged-build:yolo` needs `Bash(git commit:*)` and `Bash(git add:*)`**, since
  it commits each stage rather than describing the commit for you. Without them the
  run stops on the first permission prompt, which defeats the point of running
  unattended. It never needs push, merge, rebase, or branch deletion, so there is no
  reason to allow those.

## License

MIT
