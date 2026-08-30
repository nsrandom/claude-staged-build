# Validating with a non-Claude model (design note — not shipped)

**Nothing in this document is implemented.** It records how a `gemini-worker`
agent would work, what it would cost you, and why it is not in the plugin.

## Why you might want it

The validator exists to check the implementer's work without inheriting its
assumptions. Today that independence is structural: the validator gets a
different model, a fresh context, and only the stage file. But implementer and
validator are still both Claude, so a blind spot in the family — a shared
misreading of an ambiguous spec, a shared confidence in a wrong idiom — can pass
through both.

Routing the validator to a genuinely different model closes that gap. It is the
one role where a second opinion from outside the family is worth real friction,
because the validator only ever *reports*; it never writes code, so a weaker or
differently-behaved model cannot damage the repo.

## Shape

A cheap Claude agent on `haiku` acts as a driver. It does no reasoning about the
code — it shells out to the Gemini CLI, then returns the raw output. The judgment
is Gemini's; the Claude agent is plumbing.

```markdown
---
name: gemini-worker
description: Runs a prompt through the Gemini CLI and returns its raw output.
tools: [Bash, Read]
model: haiku
---

Send the prompt you were given to the Gemini CLI and return what it says.

1. Write the prompt to a temporary file.
2. Run: `gemini -m gemini-3.7-flash -p "$(cat <tmpfile>)"`
3. Return Gemini's output **verbatim**, inside a fenced block.

Do not summarise it. Do not correct it. Do not add your own assessment of the
code, and do not answer from your own knowledge if the CLI fails — if it errors
or is missing, report the exact error and stop. Your value here is that the
judgment is not yours.
```

The orchestrator would pass it the same payload the validator gets — the stage
file and nothing else — wrapped in the validator's own instructions, and would
read `VERDICT: PASS` / `VERDICT: FAIL` out of the returned text exactly as it does
now.

## What it costs you

- **A permission grant.** `Bash(gemini:*)` must be allowed, which lets the agent
  run an arbitrary external binary. The rest of this plugin needs no
  system-level permissions and no external binaries; this breaks that property,
  which is the main reason it is not shipped.
- **The CLI installed and authenticated.** `gemini` on `PATH`, logged in, with
  quota. When it is missing the stage fails at validation with an error that has
  nothing to do with your code.
- **Cost and latency on someone else's meter**, outside your Claude billing.
- **Weaker instruction-following.** The `VERDICT:` line is a contract. A model
  outside the family honours it less reliably, so the orchestrator needs a
  fallback for a missing or malformed verdict — a missing verdict must be treated
  as FAIL, never as a pass.

## Why it cannot live in this plugin

Plugin agents ignore `hooks`, `mcpServers`, and `permissionMode` frontmatter.
A bundled `gemini-worker` therefore cannot carry its own permission grant — the
user must add `Bash(gemini:*)` to their own settings by hand, and a plugin that
silently needs a permission it cannot declare is a bad plugin.

If the agent ever needs MCP servers or hooks — say, to reach a Gemini MCP endpoint
instead of the CLI — it must live in `~/.claude/agents/gemini-worker.md` as a
personal agent, where those fields are honoured. At that point it is no longer
distributable as part of this plugin, and the user installs it themselves.

## The `pipeline.json` hook

`pipeline.json` is already shaped for this. A role's value is a **string** today,
read as a model alias:

```json
{ "validator": "sonnet" }
```

The reserved extension is an **object** naming an agent instead:

```json
{ "validator": { "agent": "gemini-worker" } }
```

The rule, when it is built: a string means *use the plugin's agent for this role,
on this model*; an object with `agent` means *delegate this role to that agent
instead*, with an optional `model` for the driver. Existing configs stay valid
because they are all strings.

The orchestrator does not read the object form yet. Writing one today is ignored,
and the role falls back to its default model — so the config shape is forward
compatible, but the path is not live.
