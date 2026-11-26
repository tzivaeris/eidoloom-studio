"""Test 26: load_contacts() must read every row of contacts.csv the way a real CSV reader does."""
import csv
import subprocess
import sys

from common import check, finish

try:
    from contacts import load_contacts
except Exception as exc:  # noqa: BLE001
    check("contacts imports", False, repr(exc))
    finish()

with open("contacts.csv", newline="", encoding="utf-8") as handle:
    want = [dict(r) for r in csv.DictReader(handle)]
try:
    got = [dict(r) for r in load_contacts("contacts.csv")]
except Exception as exc:  # noqa: BLE001
    got = []
    check("load_contacts runs", False, repr(exc))
check("12 rows are loaded", len(got) == len(want) == 12, f"got {len(got)}")
for i, (g, w) in enumerate(zip(got, want), 1):
    check(f"row {i} ({w['name'][:20]!r}) matches", g == w, f"{g} != {w}")
check("contacts.csv is unchanged", open("contacts.csv", "rb").read() == open("/fixture/contacts.csv", "rb").read())
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the project's tests pass", r.returncode == 0, r.stderr[-300:])
finish()
