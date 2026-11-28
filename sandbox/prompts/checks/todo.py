"""Test 13: the final project must still follow the rules set in the first message."""
import json
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile

from common import check, finish

SOURCE = os.getcwd()
SCRIPT = os.path.join(SOURCE, "todo.py")
if not os.path.exists(SCRIPT):
    check("todo.py exists", False)
    finish()
work = tempfile.mkdtemp()


def todo(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=work, capture_output=True, text=True, timeout=20)


def ids(result):
    return [int(m) for m in re.findall(r"^\s*#?(\d+)\b", result.stdout, re.M)]


check("add works", todo("add", "first").returncode == 0)
check("add with priority works", todo("add", "urgent", "--priority", "high").returncode == 0)
todo("add", "third", "--priority", "low")
check("list sorts high first, then by id", ids(todo("list")) == [2, 1, 3], todo("list").stdout)
check("search ignores case", "URGENT".lower() in todo("search", "URGENT").stdout.lower())
check("done hides a task from list", todo("done", "1").returncode == 0 and 1 not in ids(todo("list")))
check("list --all shows it", 1 in ids(todo("list", "--all")))

for label, args in (("done on an unknown id", ("done", "999")), ("delete on an unknown id", ("delete", "999"))):
    result = todo(*args)
    check(f"{label}: the error rule still holds", result.returncode == 1 and result.stderr.startswith("todo: ") and not result.stdout.strip(),
          f"exit {result.returncode}, stderr {result.stderr[:80]!r}, stdout {result.stdout[:80]!r}")

check("delete works", todo("delete", "3").returncode == 0)
check("the id rule still holds: a deleted id is never reused", todo("add", "next").returncode == 0 and max(ids(todo("list", "--all"))) == 4, todo("list", "--all").stdout)
check("storage was renamed to store.json", os.path.exists(os.path.join(work, "store.json")) and not os.path.exists(os.path.join(work, "tasks.json")))
with open(os.path.join(work, "store.json"), encoding="utf-8") as handle:
    stored = handle.read()
check("the date rule still holds: created is an ISO date", re.search(r'"created":\s*"\d{4}-\d{2}-\d{2}"', stored), stored[:120])


migrate = tempfile.mkdtemp()
shutil.copy(os.path.join(work, "store.json"), os.path.join(migrate, "tasks.json"))
result = subprocess.run([sys.executable, SCRIPT, "list", "--all"], cwd=migrate, capture_output=True, text=True, timeout=20)
check("an old tasks.json is moved over", result.returncode == 0 and "urgent" in result.stdout, result.stderr[:100])

imports = set()
for root, dirs, names in os.walk(SOURCE):
    for name in names:
        if name.endswith(".py"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
            try:                           # the real imports, not "from it." in a docstring (bench v3, 2026-11-28)
                tree = ast.parse(text)
                imports |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
            except SyntaxError:
                imports |= set(re.findall(r"^\s*(?:from|import)\s+([a-zA-Z_]\w*)", text, re.M))
            check(f"{name} no longer mentions tasks.json except to migrate", name == "todo.py" or "migrat" in text.lower() or "tasks.json" not in text)
outside = {name for name in imports if name not in sys.stdlib_module_names and not os.path.exists(os.path.join(SOURCE, name + ".py")) and name not in ("tests", "todo")}
check("standard library only", not outside, outside)
tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=SOURCE, capture_output=True, text=True, timeout=60)
check("the project's tests pass", tests.returncode == 0 and "Ran 0 tests" not in tests.stderr, tests.stderr[-300:])
finish()
