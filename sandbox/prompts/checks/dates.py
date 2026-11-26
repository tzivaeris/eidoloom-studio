"""Test 25: days_until_birthday() fails on Feb 29 and on the birthday itself. Fix it and add tests."""
import ast
import glob
import subprocess
import sys
from datetime import date, timedelta

from common import check, finish

try:
    from dates import days_until_birthday
except Exception as exc:  # noqa: BLE001
    check("dates imports", False, repr(exc))
    finish()


def leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def expected(b, t):
    """Independent version: scan forward day by day to the first day that is the birthday."""
    m, d = b.month, b.day
    if (m, d) == (2, 29):
        def hit(x):
            return (x.month, x.day) == ((2, 29) if leap(x.year) else (2, 28))
    else:
        def hit(x):
            return (x.month, x.day) == (m, d)
    n = 0
    cur = t
    while not hit(cur):
        cur += timedelta(days=1)
        n += 1
    return n


CASES = [
    (date(2000, 2, 29), date(2025, 1, 1)), (date(2000, 2, 29), date(2025, 2, 28)), (date(2000, 2, 29), date(2025, 3, 1)),
    (date(2000, 2, 29), date(2024, 2, 29)), (date(2000, 2, 29), date(2024, 2, 28)), (date(2000, 2, 29), date(2024, 3, 1)),
    (date(2000, 2, 29), date(2023, 12, 31)), (date(2000, 2, 29), date(2027, 3, 1)), (date(1996, 2, 29), date(2099, 3, 1)),
    (date(1996, 2, 29), date(2100, 1, 1)), (date(1996, 2, 29), date(2103, 3, 1)),
    (date(1990, 6, 10), date(2025, 6, 10)), (date(1990, 6, 10), date(2025, 6, 11)), (date(1990, 6, 10), date(2025, 6, 9)),
    (date(1990, 1, 1), date(2025, 12, 31)), (date(1990, 1, 1), date(2025, 1, 1)), (date(1990, 1, 1), date(2024, 12, 31)),
    (date(1985, 12, 31), date(2025, 12, 31)), (date(1985, 12, 31), date(2026, 1, 1)), (date(1985, 12, 31), date(2023, 1, 1)),
    (date(2000, 3, 1), date(2024, 2, 29)), (date(2000, 3, 1), date(2025, 2, 28)), (date(2000, 2, 28), date(2024, 2, 28)),
    (date(2000, 2, 28), date(2024, 2, 29)), (date(2000, 2, 28), date(2023, 3, 1)),
]
bad = []
for b, t in CASES:
    try:
        got = days_until_birthday(b, t)
    except Exception as exc:  # noqa: BLE001
        got = repr(exc)
    want = expected(b, t)
    if got != want:
        bad.append(f"({b}, {t}) want {want} got {got}")
check(f"{len(CASES)} date cases are right", not bad, "; ".join(bad[:4]))
check("birthday today is 0", days_until_birthday(date(1990, 7, 4), date(2025, 7, 4)) == 0)
check("Feb 29 birthday does not raise in a non-leap year", not any("ValueError" in b for b in bad))


def count_tests(root):
    total = 0
    for path in glob.glob(root + "/tests/**/*.py", recursive=True):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        total += sum(isinstance(n, ast.FunctionDef) and n.name.startswith("test") for n in ast.walk(tree))
    return total


check("tests were added", count_tests(".") > count_tests("/fixture"), f"{count_tests('.')} vs {count_tests('/fixture')}")
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("all tests pass", r.returncode == 0, r.stderr[-300:])
finish()
