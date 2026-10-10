#!/usr/bin/env python3
"""Write or print one role's context bundle for a staged-build stage.

The root orchestrator never loads stage material into its own context. Instead it
hands each subagent a command line for this script; the subagent runs it first and
gets exactly the files its role needs, each under a `=== path ===` header.

With `--out`, the bundle goes to a file — by default
specs/<feature>/stages/.handoff-NN-<role>.md — and stdout carries only that path
and a manifest of the parts with approximate token counts. The agent then Reads
the file once. Without it, the bundle is printed, as before; a large one exceeds
Claude Code's Bash output limit and costs the agent an extra Read anyway.

Roles:

  stage-architect  SPEC.md, STATE.md, SESSION_STATE.json in full, plus the
                   feature's architecture.md and the previous stage's report,
                   trimmed to its **Commit:** line and its Behaviour as built and
                   Superseded sections — architecture.md carries the rest; pass
                   --full-previous-report for all of it. For the first stage both
                   are optional and a one-line note is printed instead. For a
                   later stage the previous report is required; a feature that
                   predates the running architecture.md gets the Summary and
                   Changes sections of every earlier report in its place.
  stage-runner     the stage-architect bundle, plus this stage's NN-slug.md and
                   NN-slug.detail.md.
  approval         only what `next` shows the user before running a stage: the
                   contract's acceptance criteria, verification command and files
                   expected to change; the detail file's test plan and [large]
                   step titles; and its Autonomous decisions section, verbatim,
                   whatever entry format it uses.

Exits 1 when a required file is missing, naming every missing file on stderr.
Standard library only.
"""

import argparse
import glob
import os
import re
import subprocess
import sys

ROLES = ("stage-architect", "stage-runner", "approval")

CHARS_PER_TOKEN = 4  # rough; the manifest only needs the order of magnitude

# The parts of a previous report that architecture.md does not already carry.
PREVIOUS_REPORT_SECTIONS = ["Behaviour as built", "Superseded"]

# A stage contract is `NN-slug.md` or `NN-a-slug.md`; every sibling file
# (.detail.md, .report.md, .wip.md, .findings.md) has a second dot.
CONTRACT_RE = re.compile(r"^(\d+(?:-[a-z])?)-[^.]+\.md$")


class Missing(Exception):
    pass


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def block(path, body):
    return "=== %s ===\n%s\n" % (path, body.rstrip("\n"))


def note(text):
    return "(note: %s)\n" % text


def sections(text, wanted):
    """Return {wanted-prefix: section text} for level-2 headings starting with a prefix."""
    found = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^##\s+(.*)$", line)
        prefix = None
        if m:
            title = m.group(1).strip().lower()
            prefix = next((w for w in wanted if title.startswith(w.lower())), None)
        if prefix is None or prefix in found:
            i += 1
            continue
        j = i + 1
        fence = False
        while j < len(lines):
            if lines[j].lstrip().startswith("```"):
                fence = not fence
            elif not fence and re.match(r"^#{1,2}\s", lines[j]):
                break
            j += 1
        found[prefix] = "\n".join(lines[i:j]).rstrip()
        i = j
    return found


class Feature:
    def __init__(self, repo, feature, stage):
        self.repo = repo
        self.rel = os.path.join("specs", feature)
        self.dir = os.path.join(repo, self.rel)
        self.stage = stage
        self.missing = []

    def path(self, *parts):
        return os.path.join(self.rel, *parts)

    def abs(self, rel):
        return os.path.join(self.repo, rel)

    def require(self, rel):
        if not os.path.isfile(self.abs(rel)):
            self.missing.append(rel)
            return None
        return read(self.abs(rel))

    def stage_files(self):
        """All contracts under stages/, as (key, filename), sorted by key."""
        stages = os.path.join(self.dir, "stages")
        out = []
        for name in os.listdir(stages) if os.path.isdir(stages) else []:
            m = CONTRACT_RE.match(name)
            if m:
                out.append((m.group(1), name))
        return sorted(out)

    def reports(self):
        return sorted(
            os.path.basename(p)
            for p in glob.glob(os.path.join(self.dir, "stages", "*.report.md"))
        )

    def contract(self):
        """This stage's contract filename, or None (recorded as missing)."""
        names = [n for k, n in self.stage_files() if k == self.stage]
        if len(names) == 1:
            return names[0]
        if not names:
            self.missing.append(self.path("stages", "%s-<slug>.md" % self.stage))
        else:
            self.missing.append(
                "%s (ambiguous: %s)" % (self.path("stages", "%s-*.md" % self.stage), ", ".join(names))
            )
        return None

    def earlier(self):
        """Report filenames of stages ordered before this one."""
        own = self.stage + "-"
        return [r for r in self.reports() if r < own and not r.startswith(own)]

    def is_first(self):
        if self.earlier():
            return False
        own = self.stage + "-"
        return not any(k + "-" < own for k, _ in self.stage_files())


def previous_report(rel, text, full):
    """The previous stage's report: full, or trimmed to what architecture.md lacks."""
    if full:
        return block(rel, text)
    commit = next((l.strip() for l in text.splitlines() if l.strip().startswith("**Commit:**")), None)
    found = sections(text, PREVIOUS_REPORT_SECTIONS)
    label = "Commit, Behaviour as built, Superseded"
    if not found:
        # A report written before 1.3.0 has neither section; its Summary and
        # Changes are the nearest equivalent.
        found = sections(text, ["Summary", "Changes"])
        label = "Commit, Summary, Changes — it predates Behaviour as built"
    parts = ([commit] if commit else []) + list(found.values())
    return block("%s (%s)" % (rel, label), "\n\n".join(parts) or "(no such sections)")


def architect_bundle(f, full_previous=False):
    out = []
    for name in ("SPEC.md", "STATE.md", "SESSION_STATE.json"):
        rel = f.path(name)
        text = f.require(rel)
        if text is not None:
            out.append(block(rel, text))

    arch = f.path("architecture.md")
    prev = f.earlier()
    first = f.is_first()

    if os.path.isfile(f.abs(arch)):
        out.append(block(arch, read(f.abs(arch))))
    elif first:
        out.append(note("%s does not exist yet; this is the first stage" % arch))
    else:
        out.append(
            note(
                "%s does not exist yet; this feature predates the running file, so the "
                "Summary and Changes of every earlier report follow instead" % arch
            )
        )
        for name in prev:
            rel = f.path("stages", name)
            parts = sections(read(f.abs(rel)), ["Summary", "Changes"])
            out.append(block(rel + " (Summary and Changes)", "\n\n".join(parts.values()) or "(no Summary or Changes section)"))

    if prev:
        rel = f.path("stages", prev[-1])
        out.append(previous_report(rel, read(f.abs(rel)), full_previous))
    elif first:
        out.append(note("no previous stage report; this is the first stage"))
    else:
        f.missing.append(f.path("stages", "<previous stage>.report.md"))
    return out


def runner_bundle(f, full_previous=False):
    out = architect_bundle(f, full_previous)
    name = f.contract()
    if name:
        for rel in (f.path("stages", name), f.path("stages", name[:-3] + ".detail.md")):
            text = f.require(rel)
            if text is not None:
                out.append(block(rel, text))
    return out


def approval_bundle(f, full_previous=False):
    name = f.contract()
    if not name:
        return []
    out = []
    rel = f.path("stages", name)
    text = f.require(rel)
    if text is not None:
        found = sections(text, ["Acceptance criteria", "Verification", "Files expected to change"])
        for key in ("Acceptance criteria", "Verification", "Files expected to change"):
            out.append(block("%s § %s" % (rel, key), found.get(key, "(section not found)")))

    rel = f.path("stages", name[:-3] + ".detail.md")
    text = f.require(rel)
    if text is not None:
        found = sections(text, ["Task breakdown", "Test plan", "Autonomous decisions"])
        steps = [
            l.strip()
            for l in found.get("Task breakdown", "").splitlines()
            if "[large]" in l
        ]
        out.append(block("%s § Test plan" % rel, found.get("Test plan", "(section not found)")))
        out.append(block("%s § [large] steps" % rel, "\n".join(steps) or "none"))
        out.append(block("%s § Autonomous decisions" % rel, found.get("Autonomous decisions", "(section not found)")))
    return out


def label_of(part):
    first = part.splitlines()[0] if part else ""
    if first.startswith("=== ") and first.endswith(" ==="):
        return first[4:-4]
    return first.strip()


def manifest(parts):
    lines = []
    total = 0
    for part in parts:
        tokens = len(part) // CHARS_PER_TOKEN
        total += tokens
        lines.append("  ~%6d  %s" % (tokens, label_of(part)))
    return lines, total


def git_ignored(repo, rel):
    """True/False from `git check-ignore`, or None outside a git repo."""
    try:
        done = subprocess.run(
            ["git", "-C", repo, "check-ignore", "-q", rel],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    return {0: True, 1: False}.get(done.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Write or print a staged-build role's context bundle, one "
        "`=== path ===` header per part. Run from the repository root.",
        epilog="Exits 1 if a required file is missing. architecture.md and the previous "
        "report are optional only for the first stage.",
    )
    parser.add_argument("--feature", required=True, help="feature slug under specs/")
    parser.add_argument("--stage", required=True, help="stage number, e.g. 05 or 05-b")
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATH",
        help="extra file to append, e.g. another feature's architecture.md or an "
        "investigation's findings file (repeatable; required to exist)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print nothing but 'ok' or the missing files; for the orchestrator's "
        "pre-delegation check",
    )
    parser.add_argument(
        "--out",
        nargs="?",
        const="",
        metavar="PATH",
        help="write the bundle to PATH (default specs/<feature>/stages/"
        ".handoff-NN-<role>.md) and print only the path and a manifest of its parts",
    )
    parser.add_argument(
        "--full-previous-report",
        action="store_true",
        help="include the previous stage's whole report instead of its Commit line, "
        "Behaviour as built, and Superseded",
    )
    parser.add_argument("--repo", default=os.getcwd(), help="repository root (default: cwd)")
    args = parser.parse_args()

    stage = args.stage.strip()
    if not re.match(r"^\d+(-[a-z])?$", stage):
        parser.error("--stage must look like 05 or 05-b")

    f = Feature(os.path.abspath(args.repo), args.feature, stage)
    if not os.path.isdir(f.dir):
        print("handoff: missing %s/" % f.rel, file=sys.stderr)
        return 1

    out = {
        "stage-architect": architect_bundle,
        "stage-runner": runner_bundle,
        "approval": approval_bundle,
    }[args.role](f, args.full_previous_report)

    for rel in args.include:
        text = f.require(rel)
        if text is not None:
            out.append(block(rel, text))

    if f.missing:
        for rel in f.missing:
            print("handoff: missing required file: %s" % rel, file=sys.stderr)
        return 1
    if args.check:
        print("ok")
        return 0
    if args.out is None:
        sys.stdout.write("\n".join(out))
        return 0

    rel = args.out or f.path("stages", ".handoff-%s-%s.md" % (stage, args.role))
    dest = rel if os.path.isabs(rel) else f.abs(rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    lines, total = manifest(out)
    print(rel)
    print("~%d tokens in %d parts — Read the file once:" % (total, len(out)))
    print("\n".join(lines))
    if git_ignored(f.repo, rel) is False:
        print("warning: %s is not gitignored; add `specs/**/.handoff-*` to .gitignore" % rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
