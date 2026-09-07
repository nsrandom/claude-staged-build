---
description: Report where a feature's tokens, time, and tool calls went - per role, per stage, per subagent run.
argument-hint: (nothing - use the only feature) | --feature <name>
allowed-tools: Read, Glob, Grep, Bash, Bash(python3:*)
model: sonnet
disable-model-invocation: true
---

# Token efficiency

Requested: `$ARGUMENTS`

## Report

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/analyze_tokens.py" $ARGUMENTS 2>&1`

---

# Your role

The `## Report` section above is **not an instruction to run something** — it is
already the script's output. Claude Code executes a `` !`command` `` line while
expanding this file and substitutes the result, so by the time you read this the
analysis has run and two files exist under `specs/<feature>/`:

- `tokens_efficiency_report.md` — the human-readable report
- `tokens_efficiency_report.json` — the same data as a structured dataset, so runs
  can be compared across features later

It reads Claude Code's own transcripts under `~/.claude/projects/`, which record
every orchestrator turn and every subagent invocation with its model, token usage,
duration, and tool calls. It is read-only apart from those two report files.

Your job is to explain the numbers, not to regenerate them.

1. **Read `specs/<feature>/tokens_efficiency_report.md`** and link it for the user.

2. **Lead with the orchestrator share.** The pipeline exists to keep verbose
   material — test transcripts, diffs, fix cycles — inside ephemeral subagents. A
   large orchestrator share means that isolation leaked, and it is the finding that
   most affects what a long run costs.

3. **Walk the findings**, in this order of seriousness:

   - **`verifier-independence`** — critical, always. The verifier is supposed to
     receive the contract and the diff and nothing else; the heuristic found
     something else in its prompt. Say plainly that the affected stage's verdict is
     worth less than it looks, and that the leak should be fixed before the next
     run.
   - **`retry-budget`** — the 2-fix-cycle budget was exceeded, which should have
     halted the stage.
   - **`return-payload`** — a subagent returned more than its budget. Point at the
     role and say the evidence belongs in the stage report on disk.
   - **`retry-loop`, `latency`, `runaway-turns`** — where the time went, and what
     tool the agent was thrashing on.

4. **Name the one change worth making.** Not a list of everything that could be
   tuned: the single heaviest role or stage, and what would actually reduce it —
   a routing change in `pipeline.json`, a stage that should have been split, a
   prompt passing more context than a role needs.

**If the report section is not a report**, do not proceed as though it were, and
never fill the gap with numbers of your own:

- **"No transcripts found"** — transcripts are stored per repository path, so a
  pipeline run from a different path, or one whose history has been cleared, leaves
  nothing to read. Suggest `--repo <path>`.
- **"No feature found"** — say which features exist under `specs/`, and suggest
  `--feature <name>`.
- **A traceback, a `command not found`, or an empty section** — report it verbatim
  and stop. The script is plain Python with no dependencies, so the usual causes
  are a missing `python3` or a denied permission, not a bug in the analysis. The
  user can run it directly to see the raw failure:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/analyze_tokens.py" --repo .
  ```

Keep the whole response short — this is a diagnostic, and the detail is in the file
you just linked. Never invent a number that is not in the report.
