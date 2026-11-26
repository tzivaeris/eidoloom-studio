"""Test 36: tags on notes across add, list and storage, with old notes.json files still loading."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from common import check, finish


def sandbox_copy():
    d = tempfile.mkdtemp()
    for name in os.listdir("."):
        if name in ("tests", "__pycache__") or name.startswith("."):
            continue
        if os.path.isfile(name):
            shutil.copy(name, d)
        elif os.path.isdir(name):
            shutil.copytree(name, os.path.join(d, name), ignore=shutil.ignore_patterns("__pycache__"))
    return d


def cli(d, *args):
    return subprocess.run([sys.executable, "notes.py", *args], cwd=d, capture_output=True, text=True, timeout=30)


d = sandbox_copy()
r = [cli(d, "add", "alpha", "--tag", "red", "--tag", "big"), cli(d, "add", "bravo", "--tag", "red"), cli(d, "add", "charlie"), cli(d, "add", "delta", "--tag", "blue")]
check("add with --tag works", all(x.returncode == 0 for x in r), str([x.stderr[-100:] for x in r]))
red = cli(d, "list", "--tag", "red")
check("list --tag red shows the two red notes", red.returncode == 0 and "alpha" in red.stdout and "bravo" in red.stdout, red.stdout + red.stderr)
check("list --tag red hides the others", "charlie" not in red.stdout and "delta" not in red.stdout)
big = cli(d, "list", "--tag", "big")
check("a note with two tags is found by the second", "alpha" in big.stdout and "bravo" not in big.stdout)
none = cli(d, "list", "--tag", "nothing")
check("an unused tag lists nothing", none.returncode == 0 and "alpha" not in none.stdout)
allr = cli(d, "list")
check("list without a tag shows every note", all(w in allr.stdout for w in ("alpha", "bravo", "charlie", "delta")))
try:
    stored = json.load(open(os.path.join(d, "notes.json"), encoding="utf-8"))
    by = {n["text"]: n for n in stored}
    ok = sorted(by["alpha"].get("tags", [])) == ["big", "red"] and by["bravo"].get("tags") == ["red"] and not by["charlie"].get("tags")
    keys = all({"id", "text", "created"} <= set(n) for n in stored)
except Exception as exc:  # noqa: BLE001
    ok = keys = False
check("tags are saved in notes.json", ok)
check("notes.json keeps id, text and created", keys)
check("search still works", "bravo" in cli(d, "search", "brav").stdout)

d2 = sandbox_copy()
old = [{"id": 1, "text": "oldnote one", "created": "2025-01-01"}, {"id": 2, "text": "oldnote two", "created": "2025-01-02"}]
json.dump(old, open(os.path.join(d2, "notes.json"), "w", encoding="utf-8"))
lst = cli(d2, "list")
check("an old notes.json without tags lists fine", lst.returncode == 0 and "oldnote one" in lst.stdout and "oldnote two" in lst.stdout, lst.stderr[-200:])
tl = cli(d2, "list", "--tag", "red")
check("tag filter works on old notes", tl.returncode == 0 and "oldnote" not in tl.stdout, tl.stderr[-200:])
add = cli(d2, "add", "newer", "--tag", "red")
after = cli(d2, "list", "--tag", "red")
check("adding to an old file works and the old notes stay", add.returncode == 0 and "newer" in after.stdout and "oldnote one" in cli(d2, "list").stdout)
check("the old notes are searchable", "oldnote two" in cli(d2, "search", "two").stdout)
shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d2, ignore_errors=True)


def count_tests(root):
    n = 0
    for base, _, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                n += len(re.findall(r"^\s*def test_", open(os.path.join(base, f), encoding="utf-8").read(), re.M))
    return n


mine, theirs = count_tests("tests"), count_tests("/fixture/tests")
check("there are more test functions than before", mine > theirs, f"{mine} against {theirs}")
orig = "/fixture/tests/test_notes.py"
check("the original tests still exist", os.path.exists(orig) and os.path.exists("tests/test_notes.py"))
text = open("tests/test_notes.py", encoding="utf-8").read() if os.path.exists("tests/test_notes.py") else ""
check("none of the original tests was removed", all(m in text for m in re.findall(r"def (test_\w+)", open(orig, encoding="utf-8").read())) if os.path.exists(orig) else False)
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=120)
check("all tests pass", t.returncode == 0 and "Ran 0 tests" not in t.stderr, t.stderr[-300:])
finish()
