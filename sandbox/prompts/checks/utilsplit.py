"""Test 39: utils.py split into a package utils/ (text, numbers, files) with every name still importable."""
import ast
import os
import subprocess
import sys
import tempfile

from common import attempt, check, finish

check("utils/ is a package", os.path.isfile("utils/__init__.py"))
check("utils.py is gone", not os.path.exists("utils.py"))
TOPICS = {"text": ["slugify", "truncate", "title_case"], "numbers": ["clamp", "percent", "round_half_up"],
          "files": ["read_lines", "write_atomic", "file_hash"]}
for mod, names in TOPICS.items():
    path = f"utils/{mod}.py"
    if not os.path.isfile(path):
        check(f"{path} exists and defines {', '.join(names)}", False, "missing")
        continue
    defined = {n.name for n in ast.parse(open(path, encoding="utf-8").read()).body if isinstance(n, ast.FunctionDef)}
    check(f"{path} defines {', '.join(names)}", set(names) <= defined, f"missing {sorted(set(names) - defined)}")
init = open("utils/__init__.py", encoding="utf-8").read() if os.path.isfile("utils/__init__.py") else ""
check("__init__.py does not hold the functions itself", not any(isinstance(n, ast.FunctionDef) for n in ast.parse(init).body))

try:
    import utils
    from utils import (clamp, file_hash, percent, read_lines, round_half_up, slugify, title_case, truncate,  # noqa: F401
                       write_atomic)
except Exception as exc:  # noqa: BLE001
    check("from utils import every original name", False, repr(exc))
    finish()
check("from utils import every original name", True)
attempt("slugify", lambda: [slugify("  Hello, Wörld 42!  "), slugify("A--B")], ["hello-w-rld-42", "a-b"])
attempt("truncate", lambda: [truncate("abcdefghij", 6), truncate("abc", 3), truncate("abcd", 3)], ["abc...", "abc", "..."])
attempt("title_case", lambda: title_case("the qUICK  fox"), "The Quick Fox")
attempt("clamp", lambda: [clamp(10, 1, 5), clamp(0, 1, 5), clamp(3, 1, 5), clamp(2.5, 1, 5)], [5, 1, 3, 2.5])
attempt("percent", lambda: [percent(2, 3), percent(5, 0), percent(1, 8)], [66.7, 0.0, 12.5])
attempt("round_half_up", lambda: [round_half_up(0.5), round_half_up(1.5), round_half_up(-0.5), round_half_up(3.49)], [1, 2, -1, 3])
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "f.txt")
    write_atomic(p, "x\ny\n")
    write_atomic(p, "z\n")
    attempt("write_atomic and read_lines", lambda: read_lines(p), ["z"])
    attempt("write_atomic leaves no temporary files", lambda: os.listdir(d), ["f.txt"])
    attempt("file_hash", lambda: file_hash(p), "c865f6c5ab8d1b0bcd383a5e1e3879d22681c96bf462c269b7581d523fbe70ab")


def same(a, b):
    return os.path.isfile(a) and os.path.isfile(b) and open(a, "rb").read() == open(b, "rb").read()


orig = [f for f in os.listdir("/fixture/tests") if f.endswith(".py")] if os.path.isdir("/fixture/tests") else []
check("tests/ is unchanged", bool(orig) and all(same(f"tests/{f}", f"/fixture/tests/{f}") for f in orig)
      and sorted(f for f in os.listdir("tests") if f.endswith(".py")) == sorted(orig))
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=120)
check("the tests pass", t.returncode == 0 and "Ran 0 tests" not in t.stderr, t.stderr[-300:])
finish()
