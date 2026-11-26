"""Test 33: fmt.py with Greek prices and dates, no locale module, and tests."""
import datetime
import glob
import os
import re
import subprocess
import sys

from common import attempt, check, finish

try:
    import fmt
except Exception as exc:  # noqa: BLE001
    check("fmt.py imports", False, repr(exc))
    finish()


def price(cents):
    return fmt.format_price(cents).replace(" ", " ").replace(" ", " ")


for cents, want in [(0, "0,00 €"), (5, "0,05 €"), (99, "0,99 €"), (100, "1,00 €"), (1000, "10,00 €"), (99999, "999,99 €"), (100000, "1.000,00 €"),
                    (123450, "1.234,50 €"), (100000000, "1.000.000,00 €"), (123456789, "1.234.567,89 €")]:
    attempt(f"format_price({cents})", lambda c=cents: price(c), want)

MONTHS = "Ιανουαρίου Φεβρουαρίου Μαρτίου Απριλίου Μαΐου Ιουνίου Ιουλίου Αυγούστου Σεπτεμβρίου Οκτωβρίου Νοεμβρίου Δεκεμβρίου".split()
for i, name in enumerate(MONTHS, 1):
    attempt(f"format_date month {i}", lambda i=i: fmt.format_date(datetime.date(2026, i, 5)), f"5 {name} 2026")
attempt("day 1 has no leading zero", lambda: fmt.format_date(datetime.date(2026, 3, 1)), "1 Μαρτίου 2026")
attempt("day 31", lambda: fmt.format_date(datetime.date(2025, 12, 31)), "31 Δεκεμβρίου 2025")

source = open("fmt.py", encoding="utf-8").read()
check("fmt.py does not import locale", not re.search(r"^\s*(import\s+[\w., ]*\blocale\b|from\s+locale\b)", source, re.M))
files = [f for f in glob.glob("**/test_*.py", recursive=True) + glob.glob("**/*_test.py", recursive=True) if os.path.isfile(f)]
check("a test file exists", bool(files))
if os.path.isdir("tests"):
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
else:
    cmd = [sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"]
tests = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
ran = "Ran 0 tests" not in tests.stderr
check("the tests pass", tests.returncode == 0 and ran, tests.stderr[-300:])
finish()
