"""Test 37: mean, median and mode added with type hints, docstrings and tests (a rule given for the whole chat)."""
import ast
import os
import subprocess
import sys

from common import attempt, check, finish

try:
    import stats
except Exception as exc:  # noqa: BLE001
    check("stats imports", False, repr(exc))
    finish()


def has(name):
    return callable(getattr(stats, name, None))


for name in ("mean", "median", "mode"):
    check(f"stats.{name} exists", has(name))
if has("mean"):
    attempt("mean([1,2,3,4])", lambda: stats.mean([1, 2, 3, 4]), 2.5)
    attempt("mean([5])", lambda: stats.mean([5]), 5)

    def empty():
        try:
            stats.mean([])
        except ValueError:
            return "ValueError"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
        return "no error"

    attempt("mean([]) raises ValueError", empty, "ValueError")
if has("median"):
    attempt("median odd", lambda: stats.median([5, 1, 3]), 3)
    attempt("median even", lambda: stats.median([4, 1, 3, 2]), 2.5)
    attempt("median of one", lambda: stats.median([7]), 7)
    attempt("median does not need sorted input or change it", lambda: (lambda v: (stats.median(v), v))([9, 1, 5]), (5, [9, 1, 5]))
if has("mode"):
    attempt("mode([1,2,2,3,3]) is the smallest of the most common", lambda: stats.mode([1, 2, 2, 3, 3]), 2)
    attempt("mode([5])", lambda: stats.mode([5]), 5)
    attempt("mode([3,3,1,1,2])", lambda: stats.mode([3, 3, 1, 1, 2]), 1)
    attempt("mode([4,4,4,1,1])", lambda: stats.mode([4, 4, 4, 1, 1]), 4)

tree = ast.parse(open("stats.py", encoding="utf-8").read())
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
for name in ("mean", "median", "mode"):
    f = funcs.get(name)
    if not f:
        check(f"{name} is defined in stats.py", False)
        continue
    a = f.args
    params = a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])
    check(f"{name}: every parameter has a type hint", bool(params) and all(p.annotation is not None for p in params))
    check(f"{name}: has a return type hint", f.returns is not None)
    check(f"{name}: has a docstring", bool((ast.get_docstring(f) or "").strip()))
texts = ""
for base, _, files in os.walk("tests"):
    for fn in files:
        if fn.endswith(".py"):
            texts += open(os.path.join(base, fn), encoding="utf-8").read()
for name in ("mean", "median", "mode"):
    check(f"tests/ mention {name}", f"stats.{name}" in texts or f"{name}(" in texts)
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=120)
check("all tests pass", t.returncode == 0 and "Ran 0 tests" not in t.stderr, t.stderr[-300:])
finish()
