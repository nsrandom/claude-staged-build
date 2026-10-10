#!/usr/bin/env python3
"""Print one role's context bundle for a staged-build stage.

The root orchestrator never loads stage material into its own context. Instead it
hands each subagent a command line for this script; the subagent runs it first and
gets exactly the files its role needs, each under a `=== path ===` header.

Roles:

  stage-architect  SPEC.md, STATE.md, SESSION_STATE.json in full, plus the
                   feature's architecture.md and the full report of the stage
                   before this one. For the first stage both of those are
                   optional and a one-line note is printed instead. For a later
                   stage the previous report is required; a feature that
                   predates the running architecture.md gets the Summary and
                   Changes sections of every earlier report in its place.
  stage-runner     the stage-architect bundle, plus this stage's NN-slug.md and
                   NN-slug.detail.md.
  approval         only what `next` shows the user before running a stage: the
                   contract's acceptance criteria, verification command and files
                   expected to change; the detail file's test plan and [large]
                   step titles; and its Autonomous decisions section.

Exits 1 when a required file is missing, naming every missing file on stderr.
Standard library only.
"""

import argparse
import glob
import os
import re
import sys

ROLES = ("stage-architect", "stage-runner", "approval")

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


def architect_bundle(f):
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
        out.append(block(rel, read(f.abs(rel))))
    elif first:
        out.append(note("no previous stage report; this is the first stage"))
    else:
        f.missing.append(f.path("stages", "<previous stage>.report.md"))
    return out


def runner_bundle(f):
    out = architect_bundle(f)
    name = f.contract()
    if name:
        for rel in (f.path("stages", name), f.path("stages", name[:-3] + ".detail.md")):
            text = f.require(rel)
            if text is not None:
                out.append(block(rel, text))
    return out


def approval_bundle(f):
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


def main():
    parser = argparse.ArgumentParser(
        description="Print a staged-build role's context bundle, one `=== path ===` "
        "header per part. Run from the repository root.",
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
    }[args.role](f)

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
    sys.stdout.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
