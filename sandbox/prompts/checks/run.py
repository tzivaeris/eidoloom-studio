#!/usr/bin/env python3
"""Scores a Strata test in a throw-away container: no network, read-only project, and a private copy to run.
Usage: python3 sandbox/prompts/checks/run.py greek_page|greek_text|cart|inventory|todo|docstrings [project folder]"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
SANDBOX = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SANDBOX)
import arena  # noqa: E402

CHECKS = ("greek_page", "greek_text", "cart", "inventory", "todo", "docstrings", "balance", "greek_words")
BASELINE = os.path.join(SANDBOX, "runs", "20261105-142217", "files")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        sys.exit(__doc__)
    name = sys.argv[1]
    project = os.path.realpath(sys.argv[2] if len(sys.argv) > 2 else arena.WORKSPACE)
    command = ["docker", "run", "--rm", *arena.LOCKDOWN, "--tmpfs", "/tmp:exec,size=1g", "-v", f"{project}:/work:ro",
               "-v", f"{HERE}:/checks:ro", "-v", f"{BASELINE}:/baseline:ro", arena.IMAGE, "timeout", "-s", "KILL", "180",
               "bash", "-c", f"cp -a /work /tmp/project && cd /tmp/project && python /checks/{name}.py"]
    result = subprocess.run(command)
    if result.returncode == 137:
        print("FAILED: the check was killed after 180 seconds, which usually means a deadlock or a hang")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
