#!/usr/bin/env python3
"""Scores a Strata test in a throw-away container: no network, read-only project, and a private copy to run.
Usage: python3 sandbox/prompts/checks/run.py NAME [project folder] [--fixture DIR] [--reply FILE]

NAME is any checks/NAME.py. Inside the container the check runs in /tmp/project (a copy of the project), with the
Tallybook baseline at /baseline, the test's original fixture at /fixture and the agent's final reply at /reply.txt
(both empty when not given). Studio's own .strata-studio folder (trash, saved messages) and __pycache__ are removed from the copy first. A check whose first lines contain "# image: universal" runs in the sandbox's universal
image (Python and Node) instead of python:3.12-slim."""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
SANDBOX = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SANDBOX)
import arena  # noqa: E402

BASELINE = os.path.join(SANDBOX, "runs", "20261105-142217", "files")
UNIVERSAL = "strata-sandbox:universal"


def names():
    return sorted(f[:-3] for f in os.listdir(HERE) if f.endswith(".py") and f not in ("run.py", "common.py"))


def image_for(name):
    with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as fh:
        head = fh.read(400)
    return UNIVERSAL if "# image: universal" in head else arena.IMAGE


def main():
    p = argparse.ArgumentParser(usage=__doc__)
    p.add_argument("name")
    p.add_argument("project", nargs="?", default=arena.WORKSPACE)
    p.add_argument("--fixture", default="")
    p.add_argument("--reply", default="")
    args = p.parse_args()
    if args.name not in names():
        sys.exit(f"unknown check {args.name!r}; one of: {', '.join(names())}")
    project = os.path.realpath(args.project)
    with tempfile.TemporaryDirectory() as empty:
        fixture = os.path.realpath(args.fixture) if args.fixture else empty
        reply = os.path.realpath(args.reply) if args.reply else os.path.join(empty, "reply.txt")
        if not args.reply:
            open(reply, "w").close()
        command = ["docker", "run", "--rm", *arena.LOCKDOWN, "--tmpfs", "/tmp:exec,size=1g", "-v", f"{project}:/work:ro",
                   "-v", f"{HERE}:/checks:ro", "-v", f"{BASELINE}:/baseline:ro", "-v", f"{fixture}:/fixture:ro",
                   "-v", f"{reply}:/reply.txt:ro", "-e", "HOME=/tmp", image_for(args.name), "timeout", "-s", "KILL", "180",
                   "bash", "-c", f"cp -a /work /tmp/project && cd /tmp/project && rm -rf .strata-studio && find . -name __pycache__ -prune -exec rm -rf {{}} + ; python3 /checks/{args.name}.py"]
        result = subprocess.run(command)
    if result.returncode == 137:
        print("FAILED: the check was killed after 180 seconds, which usually means a deadlock or a hang")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
