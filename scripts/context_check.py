#!/usr/bin/env python3
"""Report how much this session's conversation has grown, for the clear-context reminder.

Run from a slash command's `!` preamble, before the command's own turn. It reads the
current session's transcript and compares the context size of its latest main-thread
turn with that of its first: the difference is what earlier work in this session
left behind, all of which is re-sent on every turn of the command about to run.

Prints one line, starting with `fresh`, `small`, `large`, or `unknown`. Standard
library only. Never fails the command: any problem prints `unknown` and exits 0.
"""

import argparse
import glob
import json
import os
import re
import sys

DEFAULT_WARN_AT = 25000


def transcript_dir(repo):
    base = os.path.expanduser("~/.claude/projects")
    for path in (os.path.realpath(repo), os.path.abspath(repo)):
        d = os.path.join(base, re.sub(r"[^a-zA-Z0-9]", "-", path))
        if os.path.isdir(d):
            return d
    return None


def session_file(directory):
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        path = os.path.join(directory, sid + ".jsonl")
        if os.path.isfile(path):
            return path
    files = glob.glob(os.path.join(directory, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def context_sizes(path):
    """Context size of every main-thread assistant turn, in order."""
    sizes = []
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message")
            if rec.get("type") != "assistant" or rec.get("isSidechain") or not isinstance(msg, dict):
                continue
            usage = msg.get("usage") or {}
            size = sum(
                usage.get(k) or 0
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            if size:
                sizes.append(size)
    return sizes


def k(n):
    return "%dk" % round(n / 1000.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.getcwd(), help="repository root (default: cwd)")
    parser.add_argument(
        "--warn-at",
        type=int,
        default=DEFAULT_WARN_AT,
        help="tokens of growth since the session started that count as large "
        "(default: %d)" % DEFAULT_WARN_AT,
    )
    args = parser.parse_args()

    try:
        directory = transcript_dir(args.repo)
        path = session_file(directory) if directory else None
        sizes = context_sizes(path) if path else None
    except OSError:
        sizes = None

    if sizes is None:
        print("unknown — no transcript found for this session")
    elif not sizes:
        print("fresh — nothing earlier in this session")
    else:
        grown = sizes[-1] - sizes[0]
        state = "large" if grown >= args.warn_at else "small"
        print(
            "%s — about %s tokens in context, %s of it from earlier work in this "
            "session (threshold %s)" % (state, k(sizes[-1]), k(max(grown, 0)), k(args.warn_at))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
