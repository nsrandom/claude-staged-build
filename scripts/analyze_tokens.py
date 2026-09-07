#!/usr/bin/env python3
"""Token and efficiency analysis for the staged-build pipeline.

Reads Claude Code's local transcripts for this repository and reports where a
feature's tokens, wall-clock time, and tool calls actually went — per pipeline
role, per stage, and per subagent invocation.

Transcripts live under ~/.claude/projects/<slugified-repo-path>/:

    <session-id>.jsonl                       the orchestrator's own turns
    <session-id>/subagents/agent-<id>.jsonl  one file per subagent invocation
    <session-id>/subagents/agent-<id>.meta.json  {"agentType", "model", ...}

Usage:
    analyze_tokens.py [--feature NAME] [--repo PATH] [--out DIR] [--quiet]

Writes specs/<feature>/tokens_efficiency_report.md and .json, and prints a
compact summary. Read-only with respect to everything except those two files.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# A subagent that returns more than its budget defeats the point of running it in
# an isolated context, so the report calls that out by role.
RETURN_BUDGETS = {
    "implementer": 250,
    "stage-architect": 250,
    # The plan-architect's report carries the questions the user has to answer, so
    # it is legitimately larger; this only flags a genuinely bloated one.
    "plan-architect": 800,
    "verifier": None,  # the verifier's evidence is the deliverable; no budget
    "stage-runner": 1000,
}

# Heuristic markers for context that must never reach the verifier.
VERIFIER_LEAK_MARKERS = [
    ("detail.md", "implementation plan (.detail.md)"),
    ("## Autonomous decisions", "decision log / autonomy addendum"),
    ("Unattended run — how to handle", "unattended addendum"),
    ("DECISIONS_LOGGED", "implementer return payload"),
    ("VERDICT: PASS", "a prior verdict"),
]

SLOW_AGENT_SECONDS = 15 * 60
RUNAWAY_TURNS = 30
CHARS_PER_TOKEN = 4  # rough, but consistent across every number in the report


# --------------------------------------------------------------------------- io


def slugify_repo(path: str) -> str:
    """Claude Code names a project directory after its absolute path."""
    return re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(path))


def transcript_root(repo: str) -> str:
    return os.path.join(os.path.expanduser("~/.claude/projects"), slugify_repo(repo))


def read_jsonl(path: str):
    try:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (ValueError, TypeError):
                    continue
    except OSError:
        return


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def text_of(message) -> str:
    """Flatten an assistant/user message to plain text for size estimates."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            out.append(block.get("text") or "")
        elif block.get("type") == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                out.append(inner)
            elif isinstance(inner, list):
                for part in inner:
                    if isinstance(part, dict) and part.get("type") == "text":
                        out.append(part.get("text") or "")
    return "\n".join(out)


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------- feature state


def detect_feature(repo: str, requested: str | None) -> str | None:
    features = sorted(
        os.path.basename(os.path.dirname(p))
        for p in glob.glob(os.path.join(repo, "specs", "*", "STATE.md"))
    )
    if requested:
        return requested if requested in features else requested
    if len(features) == 1:
        return features[0]
    branch = git_branch(repo)
    if branch and branch in features:
        return branch
    if features:
        # Most recently touched plan wins; the user can override with --feature.
        return max(
            features,
            key=lambda f: os.path.getmtime(os.path.join(repo, "specs", f, "STATE.md")),
        )
    return None


def git_branch(repo: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo, "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or None
    except OSError:
        return None


def read_state(repo: str, feature: str) -> dict:
    """Parse the STATE.md stage table."""
    path = os.path.join(repo, "specs", feature, "STATE.md")
    state = {"path": path, "exists": False, "branch": None, "stages": []}
    if not os.path.exists(path):
        return state
    state["exists"] = True
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if line.lower().startswith("branch:"):
                state["branch"] = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 3 or not re.match(r"^\d+[a-z\-]*$", cells[0]):
                continue
            state["stages"].append(
                {"num": cells[0], "title": cells[1], "status": cells[2].lower()}
            )
    return state


def read_decisions(repo: str, feature: str) -> dict:
    path = os.path.join(repo, "specs", feature, "DECISIONS.md")
    summary = {"total": 0, "tier1": 0, "tier2": 0, "by_status": Counter()}
    if not os.path.exists(path):
        return summary
    with open(path, "r", errors="ignore") as fh:
        body = fh.read()
    for block in re.split(r"^## ", body, flags=re.M)[1:]:
        summary["total"] += 1
        tier = re.search(r"\*\*Tier:\*\*\s*([12])", block)
        if tier:
            summary["tier1" if tier.group(1) == "1" else "tier2"] += 1
        status = re.search(r"\*\*Status:\*\*\s*([^\n]+)", block)
        summary["by_status"][status.group(1).strip() if status else "unknown"] += 1
    return summary


# ------------------------------------------------------------------- transcript


class Invocation:
    """One agent run: a subagent file, or an orchestrator session."""

    def __init__(self, role, model, session, source, description=None, depth=0):
        self.role = role
        self.model = model
        self.session = session
        self.source = source
        self.description = description
        self.depth = depth
        self.stage = None
        self.turns = 0
        self.tools = Counter()
        self.input_tokens = 0
        self.cache_read = 0
        self.cache_creation = 0
        self.output_tokens = 0
        self.start = None
        self.end = None
        self.first_prompt = ""
        self.final_text = ""

    @property
    def total_tokens(self):
        return (
            self.input_tokens + self.cache_read + self.cache_creation + self.output_tokens
        )

    @property
    def duration(self):
        if self.start and self.end:
            return max(0.0, (self.end - self.start).total_seconds())
        return 0.0

    @property
    def return_tokens(self):
        return est_tokens(self.final_text)

    def note_time(self, ts):
        if not ts:
            return
        self.start = ts if self.start is None or ts < self.start else self.start
        self.end = ts if self.end is None or ts > self.end else self.end

    def ingest(self, record):
        self.note_time(parse_ts(record.get("timestamp")))
        rtype = record.get("type")
        message = record.get("message")
        if rtype == "user" and not self.first_prompt:
            self.first_prompt = text_of(message)
        if rtype != "assistant" or not isinstance(message, dict):
            return
        self.turns += 1
        if not self.model:
            self.model = message.get("model")
        usage = message.get("usage") or {}
        self.input_tokens += usage.get("input_tokens") or 0
        self.cache_read += usage.get("cache_read_input_tokens") or 0
        self.cache_creation += usage.get("cache_creation_input_tokens") or 0
        self.output_tokens += usage.get("output_tokens") or 0
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.tools[block.get("name") or "?"] += 1
        text = text_of(message)
        if text.strip():
            self.final_text = text

    def to_dict(self):
        return {
            "role": self.role,
            "model": self.model,
            "stage": self.stage,
            "description": self.description,
            "session": self.session,
            "depth": self.depth,
            "turns": self.turns,
            "duration_seconds": round(self.duration, 1),
            "tokens": {
                "input": self.input_tokens,
                "cache_read": self.cache_read,
                "cache_creation": self.cache_creation,
                "output": self.output_tokens,
                "total": self.total_tokens,
            },
            "return_tokens_estimated": self.return_tokens,
            "tools": dict(self.tools.most_common()),
        }


STAGE_PATTERNS = [
    re.compile(r"stages/(\d+[a-z\-]*)-[\w\-]+\.md"),
    re.compile(r"\bstage[ \-_]?(\d{2})\b", re.I),
]


def infer_stage(*texts):
    for text in texts:
        if not text:
            continue
        for pattern in STAGE_PATTERNS:
            found = pattern.search(text)
            if found:
                return found.group(1)
    return None


def role_of(agent_type: str | None) -> str:
    if not agent_type:
        return "unknown"
    return agent_type.split(":")[-1]


def collect(repo: str, feature: str, state: dict):
    """Gather every invocation belonging to this feature."""
    root = transcript_root(repo)
    invocations = []
    sessions_seen = []
    if not os.path.isdir(root):
        return invocations, sessions_seen, root

    branch = (state.get("branch") or "").split()[0] if state.get("branch") else None
    needle = "specs/%s" % feature

    for session_file in sorted(glob.glob(os.path.join(root, "*.jsonl"))):
        session_id = os.path.basename(session_file)[: -len(".jsonl")]
        records = list(read_jsonl(session_file))
        if not records:
            continue

        blob = "".join(
            text_of(r.get("message")) for r in records if r.get("type") == "user"
        )
        branches = {r.get("gitBranch") for r in records if r.get("gitBranch")}
        relevant = needle in blob or (branch and branch in branches)

        subagent_dir = os.path.join(root, session_id, "subagents")
        subagent_files = sorted(glob.glob(os.path.join(subagent_dir, "agent-*.jsonl")))

        # A session with staged-build subagents belongs to the pipeline even if the
        # feature slug never appears in a user turn.
        metas = {}
        for agent_file in subagent_files:
            meta_path = agent_file[: -len(".jsonl")] + ".meta.json"
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", errors="ignore") as fh:
                        meta = json.load(fh)
                except (OSError, ValueError):
                    meta = {}
            metas[agent_file] = meta
            if str(meta.get("agentType", "")).startswith("staged-build:"):
                relevant = relevant or needle in blob or bool(branch and branch in branches)

        if not relevant:
            continue

        sessions_seen.append(session_id)

        orchestrator = Invocation(
            role="orchestrator",
            model=None,
            session=session_id,
            source=os.path.relpath(session_file, root),
        )
        for record in records:
            if record.get("isSidechain"):
                continue
            orchestrator.ingest(record)
        if orchestrator.turns:
            invocations.append(orchestrator)

        for agent_file in subagent_files:
            meta = metas.get(agent_file, {})
            agent_type = meta.get("agentType")
            inv = Invocation(
                role=role_of(agent_type),
                model=meta.get("model"),
                session=session_id,
                source=os.path.relpath(agent_file, root),
                description=meta.get("description"),
                depth=meta.get("spawnDepth") or 1,
            )
            for record in read_jsonl(agent_file):
                inv.ingest(record)
            # plan-architect works on the whole feature, not on a stage, even when
            # its prompt happens to mention one.
            if inv.role != "plan-architect":
                inv.stage = infer_stage(inv.description, inv.first_prompt)
            if inv.turns:
                invocations.append(inv)

    return invocations, sessions_seen, root


# ---------------------------------------------------------------------- analysis


def analyse(invocations, state):
    by_role = defaultdict(
        lambda: {
            "invocations": 0,
            "turns": 0,
            "tokens": 0,
            "output_tokens": 0,
            "duration_seconds": 0.0,
            "tools": Counter(),
            "models": Counter(),
            "return_tokens": [],
        }
    )
    for inv in invocations:
        bucket = by_role[inv.role]
        bucket["invocations"] += 1
        bucket["turns"] += inv.turns
        bucket["tokens"] += inv.total_tokens
        bucket["output_tokens"] += inv.output_tokens
        bucket["duration_seconds"] += inv.duration
        bucket["tools"].update(inv.tools)
        if inv.model:
            bucket["models"][inv.model] += 1
        if inv.role != "orchestrator":
            bucket["return_tokens"].append(inv.return_tokens)

    by_stage = defaultdict(
        lambda: {"tokens": 0, "duration_seconds": 0.0, "roles": Counter(), "turns": 0}
    )
    for inv in invocations:
        if inv.role == "orchestrator" or not inv.stage:
            continue
        bucket = by_stage[inv.stage]
        bucket["tokens"] += inv.total_tokens
        bucket["duration_seconds"] += inv.duration
        bucket["turns"] += inv.turns
        bucket["roles"][inv.role] += 1

    findings = []

    # Compact-return invariant.
    for inv in invocations:
        budget = RETURN_BUDGETS.get(inv.role)
        if budget and inv.return_tokens > budget:
            findings.append(
                {
                    "kind": "return-payload",
                    "severity": "warning",
                    "role": inv.role,
                    "stage": inv.stage,
                    "detail": "returned ~%d tokens against a %d-token budget; the "
                    "verbose material belongs in the stage report on disk"
                    % (inv.return_tokens, budget),
                }
            )

    # Verifier independence.
    for inv in invocations:
        if inv.role != "verifier":
            continue
        for marker, label in VERIFIER_LEAK_MARKERS:
            if marker in inv.first_prompt:
                findings.append(
                    {
                        "kind": "verifier-independence",
                        "severity": "critical",
                        "role": inv.role,
                        "stage": inv.stage,
                        "detail": "prompt appears to contain %s; the verifier must "
                        "receive only the contract and the diff" % label,
                    }
                )
                break

    # Retry loops.
    retries = Counter()
    for inv in invocations:
        if inv.role == "implementer" and inv.stage:
            retries[inv.stage] += 1
    for stage, count in sorted(retries.items()):
        if count > 1:
            findings.append(
                {
                    "kind": "retry-loop",
                    "severity": "warning",
                    "role": "implementer",
                    "stage": stage,
                    "detail": "%d implementer invocations (%d fix cycle%s)"
                    % (count, count - 1, "" if count == 2 else "s"),
                }
            )
        if count > 3:
            findings.append(
                {
                    "kind": "retry-budget",
                    "severity": "critical",
                    "role": "implementer",
                    "stage": stage,
                    "detail": "%d invocations exceeds the 2 fix-cycle budget" % count,
                }
            )

    # Latency and runaway turns.
    for inv in invocations:
        if inv.role == "orchestrator":
            continue
        if inv.duration > SLOW_AGENT_SECONDS:
            findings.append(
                {
                    "kind": "latency",
                    "severity": "warning",
                    "role": inv.role,
                    "stage": inv.stage,
                    "detail": "ran for %s" % fmt_duration(inv.duration),
                }
            )
        if inv.turns > RUNAWAY_TURNS:
            findings.append(
                {
                    "kind": "runaway-turns",
                    "severity": "warning",
                    "role": inv.role,
                    "stage": inv.stage,
                    "detail": "%d turns; check for tool thrashing in %s"
                    % (inv.turns, ", ".join(f"{k}x{v}" for k, v in inv.tools.most_common(3))),
                }
            )

    stages = state.get("stages") or []
    complete = bool(stages) and all(s["status"] == "done" for s in stages)
    return {
        "by_role": by_role,
        "by_stage": by_stage,
        "findings": findings,
        "mode": "post-feature" if complete else "mid-feature",
    }


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm %02ds" % (seconds // 60, seconds % 60)
    return "%dh %02dm" % (seconds // 3600, (seconds % 3600) // 60)


def fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return "%.1fM" % (count / 1_000_000)
    if count >= 1_000:
        return "%.1fK" % (count / 1_000)
    return str(count)


# ----------------------------------------------------------------------- report


def build_markdown(feature, repo, state, decisions, invocations, result, root):
    by_role = result["by_role"]
    by_stage = result["by_stage"]
    total = sum(b["tokens"] for b in by_role.values())
    subagent_total = sum(
        b["tokens"] for role, b in by_role.items() if role != "orchestrator"
    )
    orchestrator_total = by_role.get("orchestrator", {}).get("tokens", 0)

    lines = [
        "# Token efficiency report — %s" % feature,
        "",
        "- **Mode:** %s" % result["mode"],
        "- **Generated:** %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "- **Repository:** `%s`" % repo,
        "- **Transcripts:** `%s`" % root,
        "- **Branch:** %s" % (state.get("branch") or "(unknown)"),
        "",
        "## Totals",
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Total tokens | %s |" % fmt_tokens(total),
        "| Orchestrator tokens | %s (%.0f%%) |"
        % (
            fmt_tokens(orchestrator_total),
            100.0 * orchestrator_total / total if total else 0.0,
        ),
        "| Subagent tokens | %s (%.0f%%) |"
        % (
            fmt_tokens(subagent_total),
            100.0 * subagent_total / total if total else 0.0,
        ),
        "| Subagent invocations | %d |"
        % sum(b["invocations"] for r, b in by_role.items() if r != "orchestrator"),
        "| Wall clock (sum of agents) | %s |"
        % fmt_duration(sum(b["duration_seconds"] for b in by_role.values())),
        "",
        "The orchestrator share is the number to watch. The pipeline delegates whole "
        "stages so that transcripts stay inside ephemeral agents; a high orchestrator "
        "share means verbose material is leaking upward into the conversation.",
        "",
        "## By role",
        "",
        "| Role | Runs | Turns | Tokens | Output | Time | Median return | Model |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for role, bucket in sorted(
        by_role.items(), key=lambda kv: kv[1]["tokens"], reverse=True
    ):
        returns = sorted(bucket["return_tokens"])
        median = returns[len(returns) // 2] if returns else 0
        budget = RETURN_BUDGETS.get(role)
        median_cell = "~%d" % median if returns else "—"
        if budget and median > budget:
            median_cell += " / %d ⚠" % budget
        elif budget:
            median_cell += " / %d" % budget
        lines.append(
            "| `%s` | %d | %d | %s | %s | %s | %s | %s |"
            % (
                role,
                bucket["invocations"],
                bucket["turns"],
                fmt_tokens(bucket["tokens"]),
                fmt_tokens(bucket["output_tokens"]),
                fmt_duration(bucket["duration_seconds"]),
                median_cell,
                ", ".join(m for m, _ in bucket["models"].most_common(2)) or "—",
            )
        )

    if by_stage:
        lines += [
            "",
            "## By stage",
            "",
            "| Stage | Status | Tokens | Turns | Time | Roles |",
            "|---|---|---|---|---|---|",
        ]
        status_by_num = {s["num"]: s["status"] for s in state.get("stages", [])}
        for stage, bucket in sorted(by_stage.items()):
            lines.append(
                "| %s | %s | %s | %d | %s | %s |"
                % (
                    stage,
                    status_by_num.get(stage, "—"),
                    fmt_tokens(bucket["tokens"]),
                    bucket["turns"],
                    fmt_duration(bucket["duration_seconds"]),
                    ", ".join(
                        "%s×%d" % (r, c) for r, c in bucket["roles"].most_common()
                    ),
                )
            )

    tools = Counter()
    for bucket in by_role.values():
        tools.update(bucket["tools"])
    if tools:
        lines += ["", "## Tool usage", "", "| Tool | Calls |", "|---|---|"]
        for name, count in tools.most_common(15):
            lines.append("| `%s` | %d |" % (name, count))

    lines += ["", "## Findings", ""]
    findings = result["findings"]
    if not findings:
        lines.append("No invariant violations, retry loops, or runaway agents found.")
    else:
        for severity in ("critical", "warning"):
            group = [f for f in findings if f["severity"] == severity]
            if not group:
                continue
            lines.append("### %s" % severity.capitalize())
            lines.append("")
            for finding in group:
                lines.append(
                    "- **%s** (`%s`%s) — %s"
                    % (
                        finding["kind"],
                        finding["role"],
                        ", stage %s" % finding["stage"] if finding["stage"] else "",
                        finding["detail"],
                    )
                )
            lines.append("")

    if decisions["total"]:
        lines += [
            "## Decisions",
            "",
            "%d logged — %d Tier 1, %d Tier 2. Status: %s."
            % (
                decisions["total"],
                decisions["tier1"],
                decisions["tier2"],
                ", ".join(
                    "%s %s" % (c, s) for s, c in decisions["by_status"].most_common()
                ),
            ),
            "",
        ]

    lines += [
        "## Per-invocation detail",
        "",
        "| Role | Stage | Turns | Tokens | Time | Return | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for inv in sorted(invocations, key=lambda i: (i.start or datetime.min.replace(tzinfo=timezone.utc))):
        lines.append(
            "| `%s` | %s | %d | %s | %s | ~%d | `%s` |"
            % (
                inv.role,
                inv.stage or "—",
                inv.turns,
                fmt_tokens(inv.total_tokens),
                fmt_duration(inv.duration),
                inv.return_tokens,
                inv.source,
            )
        )

    lines.append("")
    return "\n".join(lines)


def build_json(feature, repo, state, decisions, invocations, result, root):
    by_role = {
        role: {
            "invocations": bucket["invocations"],
            "turns": bucket["turns"],
            "tokens": bucket["tokens"],
            "output_tokens": bucket["output_tokens"],
            "duration_seconds": round(bucket["duration_seconds"], 1),
            "tools": dict(bucket["tools"].most_common()),
            "models": dict(bucket["models"].most_common()),
            "return_tokens": bucket["return_tokens"],
            "return_budget": RETURN_BUDGETS.get(role),
        }
        for role, bucket in result["by_role"].items()
    }
    return {
        "feature": feature,
        "repo": os.path.abspath(repo),
        "transcript_root": root,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": result["mode"],
        "branch": state.get("branch"),
        "stages": state.get("stages", []),
        "decisions": {
            "total": decisions["total"],
            "tier1": decisions["tier1"],
            "tier2": decisions["tier2"],
            "by_status": dict(decisions["by_status"]),
        },
        "totals": {
            "tokens": sum(b["tokens"] for b in by_role.values()),
            "orchestrator_tokens": by_role.get("orchestrator", {}).get("tokens", 0),
            "subagent_invocations": sum(
                b["invocations"] for r, b in by_role.items() if r != "orchestrator"
            ),
        },
        "by_role": by_role,
        "by_stage": {
            stage: {
                "tokens": bucket["tokens"],
                "turns": bucket["turns"],
                "duration_seconds": round(bucket["duration_seconds"], 1),
                "roles": dict(bucket["roles"]),
            }
            for stage, bucket in result["by_stage"].items()
        },
        "findings": result["findings"],
        "invocations": [inv.to_dict() for inv in invocations],
    }


def print_summary(feature, result, invocations, out_md):
    by_role = result["by_role"]
    total = sum(b["tokens"] for b in by_role.values())
    orch = by_role.get("orchestrator", {}).get("tokens", 0)
    subs = sum(b["invocations"] for r, b in by_role.items() if r != "orchestrator")
    criticals = [f for f in result["findings"] if f["severity"] == "critical"]
    warnings = [f for f in result["findings"] if f["severity"] == "warning"]

    print("staged-build — %s (%s)" % (feature, result["mode"]))
    print(
        "  %s tokens across %d subagent runs; orchestrator holds %s (%.0f%%)"
        % (
            fmt_tokens(total),
            subs,
            fmt_tokens(orch),
            100.0 * orch / total if total else 0.0,
        )
    )
    top = sorted(
        ((r, b) for r, b in by_role.items() if r != "orchestrator"),
        key=lambda kv: kv[1]["tokens"],
        reverse=True,
    )[:3]
    if top:
        print(
            "  heaviest: "
            + ", ".join("%s %s" % (r, fmt_tokens(b["tokens"])) for r, b in top)
        )
    print("  findings: %d critical, %d warning" % (len(criticals), len(warnings)))
    for finding in criticals[:3]:
        print("    ! %s (%s): %s" % (finding["kind"], finding["role"], finding["detail"]))
    print("  report: %s" % out_md)


# ------------------------------------------------------------------------- main


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--feature", help="feature slug under specs/ (auto-detected)")
    parser.add_argument("--repo", default=os.getcwd(), help="repository root")
    parser.add_argument("--out", help="directory for the report (default specs/<feature>)")
    parser.add_argument("--quiet", action="store_true", help="write files, print nothing")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    feature = detect_feature(repo, args.feature)
    if not feature:
        print(
            "No feature found. Expected specs/<feature>/STATE.md under %s.\n"
            "Run /staged-build:stage plan \"<goal>\" first, or pass --feature." % repo,
            file=sys.stderr,
        )
        return 2

    state = read_state(repo, feature)
    decisions = read_decisions(repo, feature)
    invocations, sessions, root = collect(repo, feature, state)

    if not invocations:
        print(
            "No transcripts found for %s.\nLooked in: %s\n"
            "That directory is created by Claude Code per repository path; if you ran "
            "the pipeline from a different path, pass --repo." % (feature, root),
            file=sys.stderr,
        )
        return 1

    result = analyse(invocations, state)

    out_dir = args.out or os.path.join(repo, "specs", feature)
    os.makedirs(out_dir, exist_ok=True)
    out_md = os.path.join(out_dir, "tokens_efficiency_report.md")
    out_json = os.path.join(out_dir, "tokens_efficiency_report.json")

    with open(out_md, "w") as fh:
        fh.write(build_markdown(feature, repo, state, decisions, invocations, result, root))
    with open(out_json, "w") as fh:
        json.dump(
            build_json(feature, repo, state, decisions, invocations, result, root),
            fh,
            indent=2,
        )

    if not args.quiet:
        print_summary(feature, result, invocations, out_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
