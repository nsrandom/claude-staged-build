# Changelog

All notable changes to this plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
