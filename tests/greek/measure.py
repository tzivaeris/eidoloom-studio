#!/usr/bin/env python3
"""Runs tests/greek/sentences.tsv through Check Greek's grammar rules (and LanguageTool with --lt) and prints what was
caught, missed and wrongly flagged. python3 tests/greek/measure.py [--lt] [-v]"""
import json, os, sys, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import greek_grammar as gg

use_lt, verbose = "--lt" in sys.argv, "-v" in sys.argv
rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(os.path.dirname(__file__), "sentences.tsv"), encoding="utf-8") if l.strip() and not l.startswith("#")]
caught = missed = false_alarm = clean = 0
for expect, kind, text in rows:
    found = [f"{f['kind']}: {f['message']}" for f in gg.check(text)]
    if use_lt:
        d = urllib.parse.urlencode({"text": text, "language": "el-GR"}).encode()
        found += [f"LT {m['rule']['id']}" for m in json.load(urllib.request.urlopen("http://127.0.0.1:8010/v2/check", d))["matches"]]
    if expect == "error":
        caught += bool(found); missed += not found
        if not found or verbose: print(("MISSED " if not found else "caught ") + f"[{kind}] {text}", found if verbose else "")
    else:
        false_alarm += bool(found); clean += not found
        if found: print(f"FALSE  {text}  {found}")
print(f"\nerrors caught {caught}/{caught + missed}, correct sentences flagged {false_alarm}/{false_alarm + clean}")
