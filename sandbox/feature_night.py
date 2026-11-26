#!/usr/bin/env python3
"""Measures whether Studio's rule reminders and smarter trimming help, with repeated runs on one model.

Conditions take turns within each round, so that anything drifting during the night affects them equally. Long
sessions stayed under the normal 90 000 character trimming limit on the first night, so the trimming conditions
lower it to force trimming. Writes summary.md after every run. Static checks only here: agent code runs in containers.

    python3 sandbox/feature_night.py --until 07:30 [--model flint:strata] [--rounds 4] [--root DIR]
"""
import argparse
import ast
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
TALLY = os.path.join(HERE, "runs", "20261105-142217", "files")
OFF = {"rule_reminders": False, "smart_trim": False, "delete_tool": False, "skills": False, "full_thinking": False, "no_planning_guard": False, "retry_cut_off": False, "text_tool_calls": False, "mcp": False, "plan_tool": False, "live_view": False, "script_check": False, "script_ban": False, "edit_checks": False, "stable_prompt": False, "announce_check": False, "answer_check": False, "rule_checks": False, "repeat_check": False, "session_cache": False, "ask_tool": False, "document_tool": False, "turn_guard": False, "user_tools": False, "tasks": False, "verify_on_finish": False, "backup_interval_h": 0}
TRIM_LIMIT = 30000
CONDITIONS = [
    {"name": "baseline", "features": {}, "tests": ["13", "14"]},
    {"name": "reminders", "features": {"rule_reminders": True}, "tests": ["13", "14"]},
    {"name": "trim-old", "features": {"trim_chars": TRIM_LIMIT}, "tests": ["14"]},
    {"name": "trim-new", "features": {"trim_chars": TRIM_LIMIT, "smart_trim": True}, "tests": ["14"]},
]
COMPARISONS = [("Rule reminders", "baseline", "reminders"), (f"Smarter trimming, with a {TRIM_LIMIT:,} character limit", "trim-old", "trim-new")]
SLOWEST_TEST_S = 3600        # the runner's limit for tests 13 and 14


def new_function_docstrings(work):
    """(with a Greek docstring, all) for functions added since the Tallybook baseline. Parses the code, never runs it."""
    def functions(root):
        found = {}
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".venv")]
            for name in names:
                if name.endswith(".py"):
                    path = os.path.join(folder, name)
                    try:
                        with open(path, encoding="utf-8") as handle:
                            tree = ast.parse(handle.read())
                    except (OSError, SyntaxError, ValueError):
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            found[(os.path.relpath(path, root), node.name)] = ast.get_docstring(node) or ""
        return found
    before, after = functions(TALLY), functions(work)
    new = [doc for key, doc in after.items() if key not in before]
    return sum(bool(re.search(r"[Ͱ-Ͽ]", doc)) for doc in new), len(new)


def collect(root):
    """One row per finished test, from every condition and round so far."""
    rows = []
    for condition in CONDITIONS:
        folder = os.path.join(root, condition["name"])
        for round_name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            results = os.path.join(folder, round_name, "results.json")
            if not os.path.exists(results):
                continue
            with open(results, encoding="utf-8") as handle:
                entries = json.load(handle)
            for r in entries:
                short = r["model"].split(":")[0]
                work = os.path.join(folder, round_name, short, f"test-{r['test']}", "work")
                checks = {c["label"]: c["ok"] for c in r.get("checks", [])}
                row = {"condition": condition["name"], "round": round_name, "test": r["test"], "status": r["status"], "reason": r["reason"],
                       "minutes": round(r["seconds"] / 60), "turns": r["turns"], "calls": r["calls"], "peak": r["prompt_max"], "checks": checks}
                if r["test"] == "14":
                    row["docstrings"] = new_function_docstrings(work)
                row["trimmed"] = count_trimmed(os.path.join(folder, round_name, "instance", "data", "sessions"), r["model"], r["test"])
                rows.append(row)
    return rows


def count_trimmed(sessions, model, test):
    """How many tool outputs were trimmed in that test's chat, so that a condition that never trimmed is visible."""
    prefix = "Κανόνες" if test == "14" else "Create a Python"
    total = 0
    try:
        names = os.listdir(sessions)
    except OSError:
        return 0
    for name in names:
        with open(os.path.join(sessions, name), encoding="utf-8") as handle:
            session = json.load(handle)
        messages = session.get("messages") or []
        if session.get("model") == model and messages and str(messages[0].get("content")).startswith(prefix):
            total += sum(str(m.get("content")).startswith("[older output trimmed") for m in messages if m.get("role") == "tool")
    return total


def middle(values):
    return statistics.median(values) if values else None


def describe(rows):
    """The numbers that can differ, for one condition and test."""
    done = [r for r in rows if r["reason"] in ("finished", "answered")]
    parts = [f"{len(done)} finished of {len(rows)}"]
    if not rows:
        return parts[0]
    if rows[0]["test"] == "13":
        parts.append(f"rules held in {sum(r['checks'].get('all project rules still hold', False) for r in rows)}")
        parts.append(f"remembered without opening files in {sum(r['checks'].get('remembered the rules without opening files', False) for r in rows)}")
    else:
        got = [r["docstrings"] for r in rows if r.get("docstrings") and r["docstrings"][1]]
        if got:
            parts.append("Greek docstrings " + ", ".join(f"{a}/{b}" for a, b in got) + f" (total {100 * sum(a for a, _ in got) // sum(b for _, b in got)}%)")
        parts.append(f"hidden tests passed in {sum(r['checks'].get('hidden Tallybook tests still pass', False) for r in rows)}")
    parts.append(f"median {middle([r['minutes'] for r in rows])} min, {middle([r['turns'] for r in rows])} turns, peak prompt {middle([r['peak'] for r in rows])}")
    trimmed = sum(r["trimmed"] for r in rows)
    parts.append(f"{trimmed} tool outputs trimmed")
    return "; ".join(parts)


def write_summary(root, model):
    rows = collect(root)
    lines = [f"# Reminders and trimming on {model}, updated {datetime.datetime.now():%H:%M}", "",
             "Each condition ran the same tests in fresh folders, taking turns within each round. With so few runs, only a large and consistent gap means much.", ""]
    for title, first, second in COMPARISONS:
        lines += [f"## {title}", ""]
        tests = sorted({r["test"] for r in rows if r["condition"] in (first, second)}, key=int)
        for test in tests:
            lines += [f"Test {test}:", ""]
            for name in (first, second):
                lines.append(f"- **{name}**: " + describe([r for r in rows if r["condition"] == name and r["test"] == test]))
            lines.append("")
    lines += ["## Every run", "", "| Condition | Round | Test | Result | Ended | Minutes | Turns | Docstrings | Trimmed |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        doc = "/".join(map(str, r["docstrings"])) if r.get("docstrings") else ""
        lines.append(f"| {r['condition']} | {r['round']} | {r['test']} | {r['status']} | {r['reason']} | {r['minutes']} | {r['turns']} | {doc} | {r['trimmed']} |")
    tmp = os.path.join(root, "summary.md.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp, os.path.join(root, "summary.md"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", default="07:30", help="local time to stop, HH:MM")
    parser.add_argument("--model", default="flint:strata")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--root", default=os.path.join(HERE, "runs", "features"))
    parser.add_argument("--gateway", default="http://127.0.0.1:8787")
    parser.add_argument("--port", type=int, default=8795)
    args = parser.parse_args()
    root = os.path.realpath(args.root)
    os.makedirs(root, exist_ok=True)
    hour, minute = map(int, args.until.split(":"))
    deadline = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= datetime.datetime.now():
        deadline += datetime.timedelta(days=1)
    for number in range(1, args.rounds + 1):
        offset = number - 1
        for condition in CONDITIONS[offset % len(CONDITIONS):] + CONDITIONS[:offset % len(CONDITIONS)]:
            if deadline.timestamp() - time.time() < SLOWEST_TEST_S * len(condition["tests"]):
                print(f"out of time before round {number} {condition['name']}", flush=True)
                continue
            folder = os.path.join(root, condition["name"], f"round-{number}")
            subprocess.run([sys.executable, os.path.join(HERE, "overnight.py"), "--until", args.until, "--root", folder, "--gateway", args.gateway, "--port", str(args.port),
                            "--plan", f"{args.model}={','.join(condition['tests'])}", "--features", json.dumps({**OFF, **condition["features"]})], check=False)
            write_summary(root, args.model)
    with open(os.path.join(root, "COMPLETE"), "w", encoding="utf-8") as handle:
        handle.write(datetime.datetime.now().isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
