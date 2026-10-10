"""Tests for scripts/analyze_tokens.py, on synthetic transcripts.

Runs under pytest or the standard library alone:

    python3 -m pytest tests
    python3 -m unittest discover tests
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "analyze_tokens.py")

spec = importlib.util.spec_from_file_location("analyze_tokens", SCRIPT)
at = importlib.util.module_from_spec(spec)
spec.loader.exec_module(at)

FEATURE = "widget"


def usage(total):
    return {
        "input_tokens": 0,
        "cache_read_input_tokens": total,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
    }


def assistant(msg_id, blocks, total=1000, ts="2026-10-10T10:00:00Z", branch="main"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "gitBranch": branch,
        "message": {"id": msg_id, "model": "m", "usage": usage(total), "content": blocks},
    }


def user(text, branch="main"):
    return {
        "type": "user",
        "timestamp": "2026-10-10T09:59:00Z",
        "gitBranch": branch,
        "message": {"role": "user", "content": text},
    }


def text_block(text):
    return {"type": "text", "text": text}


def tool_block(name, **inputs):
    return {"type": "tool_use", "name": name, "input": inputs}


class Workspace(unittest.TestCase):
    """A temporary HOME holding a transcript root, and a repo with specs/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "specs", FEATURE))
        self.write_state(FEATURE, FEATURE)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = at.transcript_root(self.repo)
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_state(self, feature, branch):
        path = os.path.join(self.repo, "specs", feature, "STATE.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(
                "# Plan state — %s\n\nGoal: x\nBranch: %s\n\n"
                "| # | Stage | Status |\n|---|---|---|\n| 01 | One | done |\n" % (feature, branch)
            )

    def session(self, session_id, records, subagents=()):
        with open(os.path.join(self.root, session_id + ".jsonl"), "w") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        for i, (agent_type, agent_records) in enumerate(subagents):
            sub = os.path.join(self.root, session_id, "subagents")
            os.makedirs(sub, exist_ok=True)
            with open(os.path.join(sub, "agent-%d.jsonl" % i), "w") as fh:
                for record in agent_records:
                    fh.write(json.dumps(record) + "\n")
            with open(os.path.join(sub, "agent-%d.meta.json" % i), "w") as fh:
                json.dump({"agentType": agent_type, "model": "sonnet",
                           "description": "Verify stage 01"}, fh)

    def collect(self, feature=FEATURE):
        state = at.read_state(self.repo, feature)
        return at.collect(self.repo, feature, state)


class RepeatedMessageIds(Workspace):
    def test_usage_and_turns_count_once_per_message_id(self):
        inv = at.Invocation("implementer", None, "s", "src")
        # One API message written as three records (text, tool_use, tool_use),
        # each repeating the same id and usage.
        inv.ingest(assistant("msg_1", [text_block("thinking about it")], total=500))
        inv.ingest(assistant("msg_1", [tool_block("Read", file_path="a")], total=500))
        inv.ingest(assistant("msg_1", [tool_block("Edit", file_path="a")], total=500))
        inv.ingest(assistant("msg_2", [text_block("STATUS: PASS")], total=700))
        self.assertEqual(inv.turns, 2)
        self.assertEqual(inv.total_tokens, 1200)
        # Tool calls still come from every record.
        self.assertEqual(inv.tools, {"Read": 1, "Edit": 1})
        self.assertEqual(inv.final_text, "STATUS: PASS")
        self.assertEqual(inv.return_tokens, at.est_tokens("STATUS: PASS"))

    def test_final_text_keeps_every_text_block_of_the_last_message(self):
        inv = at.Invocation("verifier", None, "s", "src")
        inv.ingest(assistant("msg_1", [text_block("early")]))
        inv.ingest(assistant("msg_2", [text_block("part one")]))
        inv.ingest(assistant("msg_2", [text_block("part two")]))
        self.assertEqual(inv.final_text, "part one\npart two")

    def test_records_without_an_id_still_count(self):
        inv = at.Invocation("implementer", None, "s", "src")
        inv.ingest(assistant(None, [text_block("a")], total=10))
        inv.ingest(assistant(None, [text_block("b")], total=10))
        self.assertEqual((inv.turns, inv.total_tokens), (2, 20))

    def test_a_forked_session_does_not_count_its_parent_twice(self):
        cmd = user("<command-name>/staged-build:stage</command-name> specs/widget/STATE.md")
        shared = [cmd, assistant("msg_1", [text_block("a")], total=1000)]
        self.session("aaaa", shared)
        self.session("bbbb", shared + [assistant("msg_2", [text_block("b")], total=300)])
        invocations, sessions, _, _ = self.collect()
        self.assertEqual(sessions, ["aaaa", "bbbb"])
        self.assertEqual(sum(i.total_tokens for i in invocations), 1300)


class SessionRelevance(Workspace):
    def test_staged_build_command_session_is_included(self):
        self.session("cmd", [
            user("<command-name>/staged-build:yolo</command-name>\nspecs/widget/STATE.md"),
            assistant("m1", [text_block("ok")]),
        ])
        _, sessions, _, excluded = self.collect()
        self.assertEqual(sessions, ["cmd"])
        self.assertEqual(excluded, [])

    def test_staged_build_subagent_session_is_included(self):
        self.session(
            "sub",
            [user("please run specs/widget"), assistant("m1", [text_block("ok")])],
            subagents=[("staged-build:verifier", [assistant("s1", [text_block("VERDICT: PASS")])])],
        )
        _, sessions, _, _ = self.collect()
        self.assertEqual(sessions, ["sub"])

    def test_session_on_the_feature_branch_that_mentions_it_is_included(self):
        self.session("branch", [
            user("look at specs/widget/SPEC.md", branch=FEATURE),
            assistant("m1", [text_block("ok")], branch=FEATURE),
        ])
        _, sessions, _, _ = self.collect()
        self.assertEqual(sessions, ["branch"])

    def test_unrelated_session_that_mentions_the_slug_is_excluded_and_listed(self):
        self.session("explore", [
            user("explore the code, then write specs/widget/PLAN.md"),
            assistant("m1", [tool_block("Write", file_path="specs/widget/PLAN.md")], total=4000),
            assistant("m2", [text_block("done")], total=1000),
        ])
        invocations, sessions, _, excluded = self.collect()
        self.assertEqual(sessions, [])
        self.assertEqual(invocations, [])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["session"], "explore")
        self.assertEqual(excluded[0]["tokens"], 5000)

    def test_session_that_never_mentions_the_feature_is_not_listed(self):
        self.session("other", [user("hello"), assistant("m1", [text_block("hi")])])
        _, sessions, _, excluded = self.collect()
        self.assertEqual((sessions, excluded), ([], []))

    def test_another_features_session_is_excluded_even_when_it_mentions_this_one(self):
        # Every preamble cats every STATE.md, so the other feature's yolo run
        # mentions this one too; it wrote only under the other feature's specs/.
        self.write_state("gadget", "gadget")
        self.session("gadget-run", [
            user("<command-name>/staged-build:yolo</command-name>\n"
                 "specs/widget/STATE.md specs/gadget/STATE.md", branch="gadget"),
            assistant("m1", [tool_block("Edit", file_path="specs/gadget/STATE.md")],
                      branch="gadget"),
        ])
        _, sessions, _, excluded = self.collect()
        self.assertEqual(sessions, [])
        self.assertIn("gadget", excluded[0]["reason"])
        _, gadget_sessions, _, _ = self.collect("gadget")
        self.assertEqual(gadget_sessions, ["gadget-run"])

    def test_excluded_sessions_appear_in_the_report(self):
        self.session("cmd", [
            user("<command-name>/staged-build:yolo</command-name> specs/widget"),
            assistant("m1", [text_block("ok")]),
        ])
        self.session("explore", [user("specs/widget/PLAN.md"), assistant("m2", [text_block("x")])])
        invocations, _, root, excluded = self.collect()
        state = at.read_state(self.repo, FEATURE)
        result = at.analyse(invocations, state)
        result["architectures"] = at.find_architectures(self.repo, FEATURE)
        decisions = at.read_decisions(self.repo, FEATURE)
        md = at.build_markdown(FEATURE, self.repo, state, decisions, invocations, result,
                               root, excluded)
        self.assertIn("**Excluded sessions:** `explore`", md)
        self.assertIn("## Cost weighting", md)


VERIFIER_FORMAT_PROMPT = """Verify stage 01 against this contract.

# Stage 01: Widget

## Goal
Widgets exist.

## Files expected to change
- src/widget.py — new
- specs/widget/stages/01-widget.detail.md — the stage edits this detail file too

## Acceptance criteria
- [ ] `widget()` returns 1

Write every command's output to specs/widget/stages/01-widget.verification.log.
Return a checklist and your findings, ending with `VERDICT: PASS` or `VERDICT: FAIL`.
"""


class VerifierLeaks(unittest.TestCase):
    def findings_for(self, prompt):
        inv = at.Invocation("verifier", None, "s", "src")
        inv.first_prompt = prompt
        inv.turns = 1
        result = at.analyse([inv], {"stages": []})
        return [f for f in result["findings"] if f["kind"] == "verifier-independence"]

    def test_format_instruction_and_detail_filename_are_not_leaks(self):
        self.assertEqual(self.findings_for(VERIFIER_FORMAT_PROMPT), [])

    def test_a_real_verdict_line_is_a_leak(self):
        prompt = VERIFIER_FORMAT_PROMPT + "\nThe last attempt said:\n\nVERDICT: FAIL\n"
        findings = self.findings_for(prompt)
        self.assertEqual(len(findings), 1)
        self.assertIn("a prior verdict", findings[0]["detail"])

    def test_previous_verdict_phrase_is_a_leak(self):
        prompt = VERIFIER_FORMAT_PROMPT + "\nThe previous verdict flagged a missing test.\n"
        self.assertEqual(len(self.findings_for(prompt)), 1)

    def test_detail_file_content_is_a_leak(self):
        prompt = VERIFIER_FORMAT_PROMPT + "\n## Task breakdown\n1. Add widget()\n"
        findings = self.findings_for(prompt)
        self.assertEqual(len(findings), 1)
        self.assertIn("implementation plan", findings[0]["detail"])

    def test_every_matching_marker_is_reported(self):
        prompt = (
            VERIFIER_FORMAT_PROMPT
            + "\n## Per-file plan\n- src/widget.py\n\nVERDICT: PASS\n\n"
            + "## Unattended run — how to handle a judgement call\n"
        )
        detail = self.findings_for(prompt)[0]["detail"]
        for label in ("implementation plan", "a prior verdict", "unattended addendum"):
            self.assertIn(label, detail)

    def test_new_addendum_wording_still_matches(self):
        for line in (
            "  **Level:** Significant",
            "- D01 · <short title> · <what you chose> · <file:line>",
            "- **Tier:** 2",
        ):
            prompt = VERIFIER_FORMAT_PROMPT + "\n" + line + "\n"
            findings = self.findings_for(prompt)
            self.assertEqual(len(findings), 1, line)
            self.assertIn("decision entries", findings[0]["detail"])

    def test_the_real_addendum_matches(self):
        here = os.path.join(HERE, "..", "commands", "yolo.md")
        with open(here) as fh:
            text = fh.read()
        start = text.index("## Unattended run")
        addendum = text[start:text.index("````", start)]
        labels = at.verifier_leaks(addendum)
        self.assertIn("unattended addendum", labels)
        self.assertIn("decision entries / unattended addendum", labels)


MIXED_DECISIONS = """# Decisions — widget

Minor is a routine convention; Significant shapes behaviour or a contract.

## Close-out (stage 04)

Not a decision.

## D1 — Old heading form (Tier 2, confirmed; PLAN D-F1)

Body.

## D2 — Old heading form, Tier 1 (Tier 1, unconfirmed)

## D3 — Old field form

- **Stage:** 01-widget
- **Tier:** 1
- **Status:** confirmed (superseded: later)

## D4 — Old field form, Tier 2

- **Tier:** 2
- **Status:** modified: use 5

## D5 — New Significant entry

- **Stage:** 02-widget
- **Level:** Significant
- **Status:** unconfirmed

## Minor decisions

- D06 · Retry budget · 3 attempts, exponential backoff · src/net/fetch.ts:12 · confirmed
- D07 · Test layout · one file per module · tests/ · rejected
"""


class Decisions(unittest.TestCase):
    def test_all_three_formats_parse(self):
        entries = at.parse_decisions(MIXED_DECISIONS)
        self.assertEqual(
            [(e["id"], e["level"], e["status"]) for e in entries],
            [
                ("D1", "Significant", "confirmed"),
                ("D2", "Minor", "unconfirmed"),
                ("D3", "Minor", "confirmed"),
                ("D4", "Significant", "modified"),
                ("D5", "Significant", "unconfirmed"),
                ("D06", "Minor", "confirmed"),
                ("D07", "Minor", "rejected"),
            ],
        )

    def test_summary_line_reports_minor_and_significant(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "specs", FEATURE))
        with open(os.path.join(tmp, "specs", FEATURE, "DECISIONS.md"), "w") as fh:
            fh.write(MIXED_DECISIONS)
        summary = at.read_decisions(tmp, FEATURE)
        self.assertEqual((summary["total"], summary["minor"], summary["significant"]), (7, 4, 3))
        line = at.decision_line(summary)
        self.assertTrue(line.startswith("7 logged — 4 Minor, 3 Significant."), line)
        self.assertIn("Minor: 2 confirmed", line)
        self.assertIn("Significant:", line)


if __name__ == "__main__":
    unittest.main()
