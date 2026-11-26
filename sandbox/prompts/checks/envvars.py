"""Test 16: the reply lists every environment variable with its default or 'required'; files unchanged."""
import filecmp
import os
import re

from common import check, finish

with open("/reply.txt", encoding="utf-8", errors="replace") as fh:
    reply = fh.read()
lines = reply.splitlines()
VARS = {"SERVICE_API_KEY": None, "SERVICE_REGION": "eu-west-1", "CACHE_DIR": "/var/cache/app", "POOL_SIZE": "8",
        "APP_DEBUG": "0", "WORKERS": "4"}
for name, default in VARS.items():
    check(f"{name} is listed", name in reply)
    if default is None:
        continue
    near = " ".join(l for l in lines if name in l)
    if name not in near:
        check(f"{name} default {default}", False, "variable missing")
        continue
    if default == "0":
        ok = bool(re.search(r"(?<![\w.])[\"'`]?0[\"'`]?(?![\w.])", near)) or "off" in near.lower() or "false" in near.lower()
    else:
        ok = default in near
    check(f"{name} default {default}", ok, near[:200])
near = "\n".join("\n".join(lines[max(0, i - 1):i + 2]) for i, l in enumerate(lines) if "SERVICE_API_KEY" in l)
check("SERVICE_API_KEY is described as required", bool(re.search(r"required|mandatory|no default|must be set|must be provided|without a default|not optional", near, re.I)), near[:200])
for dec in ("MAX_RETRIES", "TIMEOUT_SECONDS", "MAX_SIZE_MB", "PYTHONUNBUFFERED"):
    # constants that are not variables must not be listed as read variables; tolerate a mention in a "not" sentence
    hits = [l for l in lines if dec in l]
    bad = [l for l in hits if not re.search(r"\bnot\b|n't|constant|exports?|sets?|hard-?coded|isn't|aren't", l, re.I)]
    check(f"{dec} is not presented as an environment variable", not bad, bad[:1])


def same(a, b):
    cmp = filecmp.dircmp(a, b, ignore=["__pycache__"])
    if cmp.left_only or cmp.right_only or cmp.diff_files:
        return False
    return all(same(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)


check("project files unchanged", same("/fixture", "."), "differs from the fixture")
finish()
