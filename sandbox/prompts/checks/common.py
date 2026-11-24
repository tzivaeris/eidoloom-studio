"""Small scoring helper shared by the checks. Each check runs from a private copy of the project."""
import os
import sys

sys.path.insert(0, os.getcwd())
RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""), flush=True)


def attempt(label, fn, expected):
    try:
        got = fn()
    except Exception as exc:  # noqa: BLE001 - a crash in the project is a failed check
        check(label, False, f"raised {exc!r}")
        return
    check(label, got == expected, f"expected {expected!r}, got {got!r}")


def finish():
    print(f"{sum(RESULTS)}/{len(RESULTS)} checks passed", flush=True)
    sys.exit(0 if RESULTS and all(RESULTS) else 1)
