"""Test 31: five independent items from TASKS.md, each checked on its own."""
import glob
import re
import subprocess
import sys

from common import check, finish


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


py = [f for f in glob.glob("**/*.py", recursive=True) if "__pycache__" not in f]
everything = "\n".join(read(f) for f in py)
check("1: MAX_ITEMS is gone from all code and tests", not re.search(r"\bMAX_ITEMS\b", everything))
check("1: MAX_ENTRIES is defined and used", bool(re.search(r"^MAX_ENTRIES\s*=\s*5\b", read("box.py"), re.M)) and everything.count("MAX_ENTRIES") >= 4)
try:
    import box
    ok = box.is_empty([]) is True and box.is_empty(["a"]) is False and box.is_empty(box.new_box()) is True
    full = box.new_box()
    box.add_item(full, "x")
    ok = ok and box.is_empty(full) is False
except Exception as exc:  # noqa: BLE001
    ok = False
    print("   ", repr(exc))
check("2: is_empty(box) returns True for an empty box and False otherwise", ok)
msg = ""
try:
    msg = box.add_item(box.new_box(), "a")
except Exception:  # noqa: BLE001
    pass
check("3: the message says Received", "Received" in msg and "Recieved" not in everything, msg)
v = subprocess.run([sys.executable, "cli.py", "--version"], capture_output=True, text=True, timeout=30)
check("4: cli.py --version prints 1.2.0 and exits 0", v.returncode == 0 and v.stdout.strip().endswith("1.2.0") and "1.2.0" in v.stdout, f"{v.returncode} {v.stdout!r} {v.stderr[-100:]!r}")
n = subprocess.run([sys.executable, "cli.py", "a", "b"], capture_output=True, text=True, timeout=30)
check("4: cli.py still adds items", n.returncode == 0 and "Received 'a'" in n.stdout, n.stdout + n.stderr[-100:])
try:
    lines = [l.strip() for l in read(".gitignore").splitlines()]
except OSError:
    lines = []
check("5: .gitignore has *.log", "*.log" in lines, str(lines))
check("5: .gitignore keeps its old entries", all(x in lines for x in ("__pycache__/", "*.pyc", ".venv/")))
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the tests pass", t.returncode == 0, t.stderr[-300:])
finish()
