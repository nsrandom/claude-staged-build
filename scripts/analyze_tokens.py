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
# an isolated context, so the report calls that out by role. Each number is the
# contract stated in that role's own agent file; they must stay in step.
RETURN_BUDGETS = {
    "implementer": 350,
    "stage-architect": 350,
    # The plan-architect's report carries the questions the user has to answer, so
    # it is legitimately larger; this only flags a genuinely bloated one.
    "plan-architect": 800,
    # The verifier writes command output to NN-slug.verification.log and returns
    # only a checklist, its findings, and the verdict.
    "verifier": 300,
    "stage-runner": 800,
}

# Heuristic markers for context that must never reach the verifier, as regexes.
# Each one looks for the *content* of something the verifier must not see, not a
# mention of it: every verifier prompt legitimately names the verdict format, and a
# contract's file list may legitimately name a detail file.
VERIFIER_LEAK_MARKERS = [
    # The stage-architect's NN-slug.detail.md section headings. The contract has
    # its own headings (Goal, Design details, Acceptance criteria, ...), none of
    # which collide with these.
    (
        r"^#{2,3}\s*(Task breakdown|Per-file plan|Edge cases and failure modes|Do not do)\b",
        "implementation plan content (.detail.md headings)",
    ),
    (r"wip\.md", "relay checkpoint (.wip.md)"),
    (r"^#{1,3}\s*(WIP checkpoint|Where it is stuck)\b", "relay checkpoint content"),
    (r"## Autonomous decisions", "decision log / autonomy addendum"),
    # The unattended addendum's heading, and the decision-entry shapes it and the
    # log share: a Significant entry's Level line (1.5.0), a one-line Minor entry,
    # and the older Tier line.
    (r"Unattended run — how to handle", "unattended addendum"),
    (r"\*\*Level:\*\*\s*(Minor\s*\|\s*)?Significant", "decision entries / unattended addendum"),
    (r"^\s*[-*]\s*D\d+\s*·.*·", "decision entries / unattended addendum"),
    (r"\*\*Tier:\*\*\s*(1\s*\|\s*2|[12]\b)", "decision entries / unattended addendum"),
    (r"DECISION_IDS", "implementer return payload"),
    (r"DECISIONS_LOGGED", "implementer return payload"),
    # A line that *is* a verdict, rather than the return-format instruction that
    # names both verdicts inline.
    (r"^\s*VERDICT:\s*(PASS|FAIL)\s*$", "a prior verdict"),
    (r"previous verdict|prior verdict|verifier found", "a prior verdict"),
]
VERIFIER_LEAK_RES = [
    (re.compile(pattern, re.M | re.I), label) for pattern, label in VERIFIER_LEAK_MARKERS
]

# The implementer stops at 25 planner turns and relays via a WIP checkpoint rather
# than thrashing; 30 is the hard ceiling for any role.
TURN_BUDGETS = {"implementer": 25}

# One initial implementer plus at most two relays, plus at most two fix cycles.
MAX_IMPLEMENTER_INVOCATIONS = 5

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


LEVELS = ("Minor", "Significant")

# One-line Minor entry: `- D07 · title · decision · where · status`.
MINOR_LINE_RE = re.compile(r"^\s*[-*]\s*(D\d+)\s*·(.*)$")
DECISION_HEADING_RE = re.compile(r"^##\s+(D\d+)\b(.*)$")


def status_word(text: str | None) -> str:
    """`confirmed (superseded: ...)` -> `confirmed`; `modified: x` -> `modified`."""
    found = re.match(r"\s*`?([a-zA-Z]+)", text or "")
    return found.group(1).lower() if found else "unknown"


def parse_decisions(body: str) -> list:
    """Every entry in a DECISIONS.md, as {"id", "level", "status"} dicts.

    Accepts all three formats the log has had: full entries with `**Tier:** 1|2`
    (or `(Tier N, status; ...)` in the heading), full entries with
    `**Level:** Significant`, and one-line Minor entries. Tier 1 reads as Minor and
    Tier 2 as Significant. Entries are never migrated on disk.
    """
    entries = []
    current = None
    for line in body.splitlines():
        heading = DECISION_HEADING_RE.match(line)
        if heading:
            current = {"id": heading.group(1), "level": None, "status": None}
            entries.append(current)
            legacy = re.search(r"\(Tier\s*([12])(?:,\s*([^;)]+))?", heading.group(2))
            if legacy:
                current["level"] = LEVELS[int(legacy.group(1)) - 1]
                if legacy.group(2):
                    current["status"] = status_word(legacy.group(2))
            continue
        if line.startswith("#"):
            current = None
        one_line = MINOR_LINE_RE.match(line)
        if one_line and "·" in one_line.group(2):
            fields = [f.strip() for f in one_line.group(2).split("·")]
            entries.append(
                {"id": one_line.group(1), "level": "Minor", "status": status_word(fields[-1])}
            )
            current = None
            continue
        if current is None:
            continue
        level = re.search(r"\*\*Level:\*\*\s*(Minor|Significant)", line, re.I)
        tier = re.search(r"\*\*Tier:\*\*\s*([12])", line)
        if level:
            current["level"] = level.group(1).capitalize()
        elif tier:
            current["level"] = LEVELS[int(tier.group(1)) - 1]
        status = re.search(r"\*\*Status:\*\*\s*(.+)", line)
        if status:
            current["status"] = status_word(status.group(1))
    for entry in entries:
        entry["status"] = entry["status"] or "unknown"
    return entries


def read_decisions(repo: str, feature: str) -> dict:
    path = os.path.join(repo, "specs", feature, "DECISIONS.md")
    summary = {
        "total": 0,
        "minor": 0,
        "significant": 0,
        "unclassified": 0,
        "by_status": Counter(),
        "by_level": {"Minor": Counter(), "Significant": Counter(), "unclassified": Counter()},
    }
    if not os.path.exists(path):
        return summary
    with open(path, "r", errors="ignore") as fh:
        body = fh.read()
    for entry in parse_decisions(body):
        level = entry["level"] or "unclassified"
        summary["total"] += 1
        summary[level.lower()] += 1
        summary["by_status"][entry["status"]] += 1
        summary["by_level"][level][entry["status"]] += 1
    return summary


def find_architectures(repo: str, feature: str) -> dict:
    """Locate the architecture references features keep under specs/.

    Each feature has specs/<feature>/architecture.md: its entrypoints, public API,
    invariants, and config keys in about a page, rewritten by the stage-runner
    after every passing stage and finalised at cleanup. Later stages and
    downstream features read that instead of re-deriving the same facts from
    source, so the report says which ones exist and whether this feature has one.
    """
    paths = sorted(
        os.path.relpath(p, repo)
        for p in glob.glob(os.path.join(repo, "specs", "*", "architecture.md"))
    )
    own = os.path.join("specs", feature, "architecture.md")
    return {"completed_architectures": paths, "has_architecture": own in paths}


# ------------------------------------------------------------------- transcript


class Invocation:
    """One agent run: a subagent file, or an orchestrator session."""

    def __init__(self, role, model, session, source, description=None, depth=0,
                 seen_ids=None):
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
        # Claude Code writes one record per content block and repeats the same
        # message.id and usage on each, so usage and turns count once per id. A
        # forked or resumed session repeats its parent's records under a new
        # session id, so collect() shares one set across every invocation.
        self._seen_ids = set() if seen_ids is None else seen_ids
        self._final_text_id = None

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
        message_id = message.get("id")
        if message_id is None or message_id not in self._seen_ids:
            if message_id is not None:
                self._seen_ids.add(message_id)
            self.turns += 1
            usage = message.get("usage") or {}
            self.input_tokens += usage.get("input_tokens") or 0
            self.cache_read += usage.get("cache_read_input_tokens") or 0
            self.cache_creation += usage.get("cache_creation_input_tokens") or 0
            self.output_tokens += usage.get("output_tokens") or 0
        if not self.model:
            self.model = message.get("model")
        # Tool calls and text are per block, so they come from every record.
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.tools[block.get("name") or "?"] += 1
        text = text_of(message)
        if text.strip():
            # A message split across records keeps all its text blocks; a later
            # message replaces it.
            if message_id is not None and message_id == self._final_text_id:
                self.final_text += "\n" + text
            else:
                self.final_text = text
            self._final_text_id = message_id

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


STAGED_BUILD_COMMAND = "<command-name>/staged-build:"
SPECS_WRITE_RE = re.compile(r"specs/([^/\s]+)/")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def load_meta(agent_file: str) -> dict:
    meta_path = agent_file[: -len(".jsonl")] + ".meta.json"
    try:
        with open(meta_path, "r", errors="ignore") as fh:
            meta = json.load(fh)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}


def specs_writes(records) -> Counter:
    """How many Edit/Write calls each feature's specs/<feature>/ directory received."""
    writes = Counter()
    for record in records:
        message = record.get("message")
        if record.get("type") != "assistant" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            if block.get("name") not in WRITE_TOOLS:
                continue
            path = str((block.get("input") or {}).get("file_path") or "")
            found = SPECS_WRITE_RE.search(path)
            if found:
                writes[found.group(1)] += 1
    return writes


def feature_branches(repo: str) -> dict:
    """{feature: branch} from every specs/*/STATE.md that names one."""
    out = {}
    for path in glob.glob(os.path.join(repo, "specs", "*", "STATE.md")):
        name = os.path.basename(os.path.dirname(path))
        branch = read_state(repo, name).get("branch")
        if branch and not branch.startswith("("):
            out[name] = branch.split()[0]
    return out


def session_tokens(records, agent_files, seen_ids=None) -> int:
    """Deduplicated token total for a root session and all its subagents."""
    seen_ids = set() if seen_ids is None else seen_ids
    root = Invocation("orchestrator", None, None, None, seen_ids=seen_ids)
    for record in records:
        if not record.get("isSidechain"):
            root.ingest(record)
    total = root.total_tokens
    for agent_file in agent_files:
        inv = Invocation("subagent", None, None, None, seen_ids=seen_ids)
        for record in read_jsonl(agent_file):
            inv.ingest(record)
        total += inv.total_tokens
    return total


def classify_session(feature, mentions, ran_command, has_subagents, on_branch,
                     writes, branches_seen, other_branches):
    """Return (included, reason) for one root session.

    A session belongs to the feature only when it ran a /staged-build:* command,
    has a staged-build subagent, or is on the feature's branch — and mentions the
    feature. Every command preamble cats every feature's STATE.md, so a mention
    alone proves nothing; nor does a staged-build command, which may have been for
    another feature. When the session evidently worked on another feature — it
    wrote more under that feature's specs/ directory, or ran on that feature's
    branch and never on this one — it is that feature's, not this one's.
    """
    if not (mentions or on_branch):
        return False, None
    if not (ran_command or has_subagents or (on_branch and mentions)):
        return False, "no staged-build command or subagent, and not on the feature branch"
    if writes:
        top, count = writes.most_common(1)[0]
        if top != feature and writes.get(feature, 0) < count:
            return False, "worked on %s (wrote under specs/%s/)" % (top, top)
    elif not on_branch:
        for other, other_branch in sorted(other_branches.items()):
            if other != feature and other_branch in branches_seen:
                return False, "ran on %s's branch `%s`" % (other, other_branch)
    return True, None


def collect(repo: str, feature: str, state: dict):
    """Gather every invocation belonging to this feature.

    Returns (invocations, sessions_seen, root, excluded), where `excluded` lists
    the sessions that mention the feature but were judged not to belong to it,
    with their token totals and the reason, so nothing disappears silently.
    """
    root = transcript_root(repo)
    invocations = []
    sessions_seen = []
    excluded = []
    if not os.path.isdir(root):
        return invocations, sessions_seen, root, excluded

    branch = (state.get("branch") or "").split()[0] if state.get("branch") else None
    if branch and branch.startswith("("):
        branch = None  # "(not created yet)"
    other_branches = feature_branches(repo)
    needle = "specs/%s" % feature
    seen_ids = set()  # message ids already counted for this feature
    excluded_seen_ids = set()  # and for the excluded sessions, kept apart

    for session_file in sorted(glob.glob(os.path.join(root, "*.jsonl"))):
        session_id = os.path.basename(session_file)[: -len(".jsonl")]
        records = list(read_jsonl(session_file))
        if not records:
            continue

        user_records = [
            r for r in records if r.get("type") == "user" and not r.get("isSidechain")
        ]
        blob = "".join(text_of(r.get("message")) for r in user_records)
        raw_user = "".join(json.dumps(r.get("message"), ensure_ascii=False) for r in user_records)
        branches = {r.get("gitBranch") for r in records if r.get("gitBranch")}
        mentions = needle in blob or feature in blob
        on_branch = bool(branch and branch in branches)

        subagent_dir = os.path.join(root, session_id, "subagents")
        subagent_files = sorted(glob.glob(os.path.join(subagent_dir, "agent-*.jsonl")))
        metas = {agent_file: load_meta(agent_file) for agent_file in subagent_files}
        has_subagents = any(
            str(meta.get("agentType", "")).startswith("staged-build:") for meta in metas.values()
        )
        ran_command = STAGED_BUILD_COMMAND in raw_user

        writes = specs_writes(records)
        if mentions or on_branch:
            for agent_file in subagent_files:
                writes.update(specs_writes(read_jsonl(agent_file)))

        included, reason = classify_session(
            feature, mentions, ran_command, has_subagents, on_branch,
            writes, branches, other_branches,
        )
        if not included:
            if reason:
                excluded.append(
                    {
                        "session": session_id,
                        "tokens": session_tokens(records, subagent_files, excluded_seen_ids),
                        "reason": reason,
                    }
                )
            continue

        sessions_seen.append(session_id)

        orchestrator = Invocation(
            role="orchestrator",
            model=None,
            session=session_id,
            source=os.path.relpath(session_file, root),
            seen_ids=seen_ids,
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
                seen_ids=seen_ids,
            )
            for record in read_jsonl(agent_file):
                inv.ingest(record)
            # plan-architect works on the whole feature, not on a stage, even when
            # its prompt happens to mention one.
            if inv.role != "plan-architect":
                inv.stage = infer_stage(inv.description, inv.first_prompt)
            if inv.turns:
                invocations.append(inv)

    return invocations, sessions_seen, root, excluded


# ---------------------------------------------------------------------- analysis


def verifier_leaks(prompt: str) -> list:
    """Every distinct leak label whose marker matches the prompt, in marker order."""
    labels = []
    for pattern, label in VERIFIER_LEAK_RES:
        if label not in labels and pattern.search(prompt or ""):
            labels.append(label)
    return labels


def cost_shares(bucket) -> dict:
    """Percent of a role's tokens that were cache reads, cache writes, and input/output."""
    total = (
        bucket["cache_read"]
        + bucket["cache_creation"]
        + bucket["input_tokens"]
        + bucket["output_tokens"]
    )
    if not total:
        return {"cache_read": 0.0, "cache_creation": 0.0, "input_output": 0.0}
    return {
        "cache_read": 100.0 * bucket["cache_read"] / total,
        "cache_creation": 100.0 * bucket["cache_creation"] / total,
        "input_output": 100.0 * (bucket["input_tokens"] + bucket["output_tokens"]) / total,
    }


def analyse(invocations, state):
    by_role = defaultdict(
        lambda: {
            "invocations": 0,
            "turns": 0,
            "tokens": 0,
            "output_tokens": 0,
            "input_tokens": 0,
            "cache_read": 0,
            "cache_creation": 0,
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
        bucket["input_tokens"] += inv.input_tokens
        bucket["cache_read"] += inv.cache_read
        bucket["cache_creation"] += inv.cache_creation
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
                    "detail": "returned ~%d tokens against the %d-token budget for "
                    "`%s`; the verbose material belongs on disk — the stage report, "
                    "the detail file, or the verification log"
                    % (inv.return_tokens, budget, inv.role),
                }
            )

    # Verifier independence.
    for inv in invocations:
        if inv.role != "verifier":
            continue
        labels = verifier_leaks(inv.first_prompt)
        if labels:
            findings.append(
                {
                    "kind": "verifier-independence",
                    "severity": "critical",
                    "role": inv.role,
                    "stage": inv.stage,
                    "detail": "prompt appears to contain %s; the verifier must "
                    "receive only the contract and the diff" % "; ".join(labels),
                }
            )

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
                    "detail": "%d implementer invocations (%d re-entr%s — fix cycles, "
                    "relays, or both)"
                    % (count, count - 1, "y" if count == 2 else "ies"),
                }
            )
        if count > MAX_IMPLEMENTER_INVOCATIONS:
            findings.append(
                {
                    "kind": "retry-budget",
                    "severity": "critical",
                    "role": "implementer",
                    "stage": stage,
                    "detail": "%d invocations exceeds the ceiling of %d "
                    "(1 initial + at most 2 relays + at most 2 fix cycles)"
                    % (count, MAX_IMPLEMENTER_INVOCATIONS),
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
        turn_budget = TURN_BUDGETS.get(inv.role)
        if turn_budget and turn_budget < inv.turns <= RUNAWAY_TURNS:
            findings.append(
                {
                    "kind": "turn-budget",
                    "severity": "warning",
                    "role": inv.role,
                    "stage": inv.stage,
                    "detail": "%d turns against a %d-turn budget; it should have "
                    "written a WIP checkpoint and returned RELAY_REQUIRED instead "
                    "of running on" % (inv.turns, turn_budget),
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


def build_markdown(feature, repo, state, decisions, invocations, result, root, excluded=()):
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
        "- **Architectures:** %s"
        % (
            ", ".join(
                "`%s`" % p for p in result["architectures"]["completed_architectures"]
            )
            or "(none)"
        ),
        "- **Excluded sessions:** %s"
        % (
            "; ".join(
                "`%s` %s — %s" % (e["session"][:8], fmt_tokens(e["tokens"]), e["reason"])
                for e in excluded
            )
            or "none"
        ),
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
        "stages so that transcripts stay inside ephemeral agents, and hands each one a "
        "handoff.py command rather than pasted file text; a high orchestrator share "
        "means verbose material — build output, or spec files read in to paste — is "
        "reaching the conversation.",
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

    lines += [
        "",
        "## Cost weighting",
        "",
        "Token totals above weigh every token the same, but cache reads are billed at "
        "about a tenth of the input rate and cache writes at about 1.25x, so a role "
        "made mostly of cache reads costs far less than its total suggests.",
        "",
        "| Role | Cache read | Cache write | Input + output |",
        "|---|---|---|---|",
    ]
    for role, bucket in sorted(
        by_role.items(), key=lambda kv: kv[1]["tokens"], reverse=True
    ):
        shares = cost_shares(bucket)
        lines.append(
            "| `%s` | %.0f%% | %.0f%% | %.0f%% |"
            % (role, shares["cache_read"], shares["cache_creation"], shares["input_output"])
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
        lines += ["## Decisions", "", decision_line(decisions), ""]

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


def decision_line(decisions) -> str:
    """`N logged — A Minor, B Significant. Minor: 18 confirmed, ... Significant: ...`"""
    def breakdown(counter):
        return ", ".join("%d %s" % (c, s) for s, c in counter.most_common()) or "none"

    head = "%d logged — %d Minor, %d Significant" % (
        decisions["total"],
        decisions["minor"],
        decisions["significant"],
    )
    if decisions["unclassified"]:
        head += ", %d unclassified" % decisions["unclassified"]
    parts = [head + "."]
    for level in ("Minor", "Significant", "unclassified"):
        counter = decisions["by_level"][level]
        if sum(counter.values()):
            parts.append("%s: %s." % (level, breakdown(counter)))
    return " ".join(parts)


def build_json(feature, repo, state, decisions, invocations, result, root, excluded=()):
    by_role = {
        role: {
            "invocations": bucket["invocations"],
            "turns": bucket["turns"],
            "tokens": bucket["tokens"],
            "output_tokens": bucket["output_tokens"],
            "input_tokens": bucket["input_tokens"],
            "cache_read": bucket["cache_read"],
            "cache_creation": bucket["cache_creation"],
            "cost_shares_percent": {
                k: round(v, 1) for k, v in cost_shares(bucket).items()
            },
            "duration_seconds": round(bucket["duration_seconds"], 1),
            "tools": dict(bucket["tools"].most_common()),
            "models": dict(bucket["models"].most_common()),
            "return_tokens": bucket["return_tokens"],
            "return_budget": RETURN_BUDGETS.get(role),
            "turn_budget": TURN_BUDGETS.get(role),
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
        "completed_architectures": result["architectures"]["completed_architectures"],
        "has_architecture": result["architectures"]["has_architecture"],
        "stages": state.get("stages", []),
        "decisions": {
            "total": decisions["total"],
            "minor": decisions["minor"],
            "significant": decisions["significant"],
            "unclassified": decisions["unclassified"],
            "by_status": dict(decisions["by_status"]),
            "by_level": {k: dict(v) for k, v in decisions["by_level"].items()},
        },
        "excluded_sessions": list(excluded),
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


def print_summary(feature, result, invocations, out_md, excluded=()):
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
    arch = result["architectures"]
    print(
        "  Architectures: %s"
        % (", ".join(arch["completed_architectures"]) or "(none)")
    )
    if result["mode"] == "post-feature" and not arch["has_architecture"]:
        print(
            "    ! no architecture.md for this feature — it predates the per-stage"
            " file; run /staged-build:cleanup to write it"
        )
    if excluded:
        print(
            "  excluded sessions: "
            + ", ".join("%s %s" % (e["session"][:8], fmt_tokens(e["tokens"])) for e in excluded)
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
    invocations, sessions, root, excluded = collect(repo, feature, state)

    if not invocations:
        print(
            "No transcripts found for %s.\nLooked in: %s\n"
            "That directory is created by Claude Code per repository path; if you ran "
            "the pipeline from a different path, pass --repo." % (feature, root),
            file=sys.stderr,
        )
        return 1

    result = analyse(invocations, state)
    result["architectures"] = find_architectures(repo, feature)

    out_dir = args.out or os.path.join(repo, "specs", feature)
    os.makedirs(out_dir, exist_ok=True)
    out_md = os.path.join(out_dir, "tokens_efficiency_report.md")
    out_json = os.path.join(out_dir, "tokens_efficiency_report.json")

    with open(out_md, "w") as fh:
        fh.write(
            build_markdown(feature, repo, state, decisions, invocations, result, root, excluded)
        )
    with open(out_json, "w") as fh:
        json.dump(
            build_json(feature, repo, state, decisions, invocations, result, root, excluded),
            fh,
            indent=2,
        )

    if not args.quiet:
        print_summary(feature, result, invocations, out_md, excluded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
