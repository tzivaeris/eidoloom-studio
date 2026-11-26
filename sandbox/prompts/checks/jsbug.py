# image: universal
"""Test 38: npm test passes after a fix in src/, with the tests and the test script untouched."""
import json
import os
import re
import subprocess

from common import check, finish


def files(root):
    out = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for n in names:
            p = os.path.join(base, n)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


r = subprocess.run(["node", "--test"], capture_output=True, text=True, timeout=120)
text = r.stdout + r.stderr
passed = re.search(r"(?:ℹ|#) pass (\d+)", text)
failed = re.search(r"(?:ℹ|#) fail (\d+)", text)
check("node --test passes", r.returncode == 0 and failed is not None and failed.group(1) == "0", text[-400:])
check("all the tests ran", passed is not None and int(passed.group(1)) >= 5, f"pass count {passed.group(1) if passed else 'none'}")
mine, orig = files("test"), files("/fixture/test")
check("test files are unchanged", bool(orig) and mine == orig, "changed: " + ", ".join(sorted(k for k in set(mine) | set(orig) if mine.get(k) != orig.get(k))))
try:
    now = json.load(open("package.json", encoding="utf-8"))["scripts"]["test"]
    was = json.load(open("/fixture/package.json", encoding="utf-8"))["scripts"]["test"]
    check('package.json "test" is unchanged', now == was, now)
except Exception as exc:  # noqa: BLE001
    check('package.json "test" is unchanged', False, repr(exc))
check("src/scores.js still exists and exports the same names", subprocess.run(
    ["node", "-e", "const m=require('./src/scores');for(const k of ['topScores','average','range','rankOf'])if(typeof m[k]!=='function')process.exit(1)"]).returncode == 0)
finish()
