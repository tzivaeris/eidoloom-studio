"""Test 32: the user-facing messages of cli.py are Greek; names, JSON keys and exit codes are unchanged."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from common import check, finish
from greekcheck import GREEK, has_cjk, mixed_words

CLI = os.path.abspath("cli.py")
ENGLISH = ["already exists", "must not be empty", "there is no item", "Removed item", "Added item", "no items yet",
           "Keep a simple list", "Add a new item", "Show all items", "Print the items as JSON", "Remove an item by",
           "name of the item", "id of the item"]
work = tempfile.mkdtemp()


def run(*args):
    return subprocess.run([sys.executable, CLI, *args], cwd=work, capture_output=True, text=True, timeout=30)


def out(r):
    return r.stdout + r.stderr


def english_left(text):
    return [p for p in ENGLISH if p.lower() in text.lower()]


add = run("add", "milk")
dup = run("add", "milk")
empty = run("add", "  ")
lst = run("list")
listjson = run("list", "--json")
rem = run("remove", "1")
missing = run("remove", "1")
empty_list = run("list")
helps = [run(*a, "--help") for a in ([], ["add"], ["list"], ["remove"])]

check("exit codes are unchanged", [add.returncode, dup.returncode, empty.returncode, lst.returncode, rem.returncode, missing.returncode] == [0, 1, 2, 0, 0, 1],
      str([add.returncode, dup.returncode, empty.returncode, lst.returncode, rem.returncode, missing.returncode]))
check("confirmation of add is Greek", bool(GREEK.search(add.stdout)) and not english_left(add.stdout), add.stdout)
check("confirmation of remove is Greek", bool(GREEK.search(rem.stdout)) and not english_left(rem.stdout), rem.stdout)
check("empty-list message is Greek", bool(GREEK.search(empty_list.stdout)) and not english_left(empty_list.stdout), empty_list.stdout)
check("error messages go to stderr and are Greek", all(GREEK.search(r.stderr) and not english_left(r.stderr) for r in (dup, empty, missing)),
      " | ".join(r.stderr for r in (dup, empty, missing)))
check("help text is Greek, none of the English sentences left", all(GREEK.search(h.stdout) for h in helps) and not any(english_left(h.stdout) for h in helps),
      str([english_left(h.stdout) for h in helps]))
try:
    run("add", "bread")
    rows = json.loads(run("list", "--json").stdout)
    ok = bool(rows) and all(set(r) == {"id", "name", "created"} for r in rows)
except Exception as exc:  # noqa: BLE001
    ok = False
check("list --json keeps the keys id, name, created", ok)
check("command names are unchanged", all(c in helps[0].stdout for c in ("add", "list", "remove")))
check("the plain list still shows the names", "bread" in run("list").stdout)

source = open("cli.py", encoding="utf-8").read()
check("cli.py has no CJK characters", not has_cjk(source))
check("cli.py has no mixed Greek/Latin words", not mixed_words(source), str(mixed_words(source)))
check("cli.py contains Greek text", bool(GREEK.search(source)))


def same(path):
    a, b = os.path.join("tests", path), os.path.join("/fixture", "tests", path)
    return os.path.exists(a) and os.path.exists(b) and open(a, "rb").read() == open(b, "rb").read()


names = sorted(f for f in os.listdir("/fixture/tests") if f.endswith(".py")) if os.path.isdir("/fixture/tests") else []
check("the tests are unchanged", bool(names) and all(same(n) for n in names))
tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=120)
check("the tests pass", tests.returncode == 0, tests.stderr[-300:])
shutil.rmtree(work, ignore_errors=True)
finish()
