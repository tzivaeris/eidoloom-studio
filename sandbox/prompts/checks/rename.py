"""Test 27: rename calc to compute_total everywhere, and change nothing else."""
import os
import re
import subprocess
import sys

from common import attempt, check, finish


def listing(root):
    out = set()
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in names:
            if not n.endswith(".pyc"):
                out.add(os.path.relpath(os.path.join(base, n), root))
    return out


def lines(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read().split("\n")


mine, orig = listing("."), listing("/fixture")
check("no files added or removed", mine == orig, f"{sorted(mine ^ orig)}")
left = [f for f in mine if re.search(r"\bcalc\b", open(os.path.join(f), encoding="utf-8").read())]
check("no \\bcalc\\b is left anywhere", not left, str(left))
bad = []
for f in orig:
    here = f
    if here not in mine:
        bad.append(f"{f} missing")
        continue
    want = [re.sub(r"\bcalc\b", "compute_total", line) for line in lines(os.path.join("/fixture", f))]
    got = lines(here)
    if got != want:
        diffs = [(a, b) for a, b in zip(got, want) if a != b][:1]
        bad.append(f"{f}: {len(got)} lines vs {len(want)} {diffs}")
check("every file equals the original with only the word calc replaced", not bad, "; ".join(bad[:3]))
try:
    import orders
    import report
    fn = getattr(__import__("pricing"), "compute_total")
except Exception as exc:  # noqa: BLE001
    check("project imports", False, repr(exc))
    finish()
attempt("compute_total works", lambda: [fn([1, 2, 3]), fn([10, 20], 0.1), orders.order_total([(2, 5.0)], 0.2)], [6, 33.0, 12.0])
attempt("recalculate and calculator names are untouched", lambda: [orders.recalculate([(1, 10.0)], 0.5), report.summary([10, 20])], [15.0, "recalculate: 30.0 -> 33.0"])
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the tests pass", r.returncode == 0, r.stderr[-300:])
finish()
