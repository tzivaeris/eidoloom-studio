"""Test 24: paginate() drops the last partial page. Fix it and add a test, leaving the old tests alone."""
import ast
import subprocess
import sys

from common import attempt, check, finish


def test_funcs(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return {}
    return {n.name: ast.dump(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")}


try:
    from paginate import paginate
except Exception as exc:  # noqa: BLE001
    check("paginate imports", False, repr(exc))
    finish()

CASES = [
    (list(range(7)), 3, [[0, 1, 2], [3, 4, 5], [6]]),
    (list(range(6)), 3, [[0, 1, 2], [3, 4, 5]]),
    ([], 4, []),
    (list(range(3)), 1, [[0], [1], [2]]),
    (list(range(3)), 10, [[0, 1, 2]]),
    (list(range(11)), 5, [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10]]),
    (["a"], 2, [["a"]]),
]
for items, size, want in CASES:
    attempt(f"paginate(len {len(items)}, {size})", lambda i=items, s=size: [list(p) for p in paginate(i, s)], want)


def bad_size():
    try:
        paginate([1], 0)
    except ValueError:
        return True
    return False


attempt("page_size 0 still raises ValueError", bad_size, True)
old = test_funcs("/fixture/tests/test_paginate.py")
new = {}
import glob  # noqa: E402

for path in glob.glob("tests/**/*.py", recursive=True):
    new.update({k: v for k, v in test_funcs(path).items() if k not in new})
check("the original tests are still there, unchanged", old and all(new.get(k) == v for k, v in old.items()))
check("at least one test was added", len(new) > len(old), f"{len(new)} tests, was {len(old)}")
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("all tests pass", r.returncode == 0, r.stderr[-300:])
finish()
