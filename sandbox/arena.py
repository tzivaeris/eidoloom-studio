#!/usr/bin/env python3
"""Arena-style sandbox for testing the Studio agent on a full project (see sandbox/README.md).

The agent works in sandbox/workspace. Its commands and terminals run in a throw-away container that has
no network and only that folder mounted, and grading uses a second container plus hidden tests the
agent can never read. Same container limits as the Ollama Manager arena."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
WORKSPACE = os.path.join(HERE, "workspace")
GRADER = os.path.join(HERE, "grader")
REFERENCE = os.path.join(HERE, "reference")
RUNS = os.path.join(HERE, "runs")
CONFIG = os.path.join(HERE, "studio", "config.json")
IMAGE = "python:3.12-slim"
PORT = 8791
MIN_OWN_TESTS = 10

sys.path.insert(0, ROOT)
import studio  # noqa: E402

studio.APP = types.SimpleNamespace(cfg={"sandbox_image": IMAGE, "sandbox_network": "off", "sandbox_memory": "6g", "command_timeout_s": 3},
                                   data_dir=os.path.join(HERE, "studio", "data"))
LOCKDOWN = [*studio.sandbox_flags(), "--network", "none"]
WS = {"id": hashlib.sha1(WORKSPACE.encode()).hexdigest()[:12], "path": WORKSPACE, "sandbox": True}
CONTAINER = studio.sandbox_name(WS)
LEGACY_CONTAINER = "strata-sandbox"


def docker(*args, timeout=120):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def write_config():
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    cfg = {"host": "127.0.0.1", "port": PORT, "gateway": "http://127.0.0.1:8787", "workspace_roots": [WORKSPACE],
           "default_model": "keystone:strata", "max_turns": 80, "command_timeout_s": 120, "sandbox_required": True,
           "sandbox_image": IMAGE, "sandbox_network": "off", "sandbox_memory": "6g",
           "user_root": os.path.join(HERE, "studio", "users"),
           "rule_reminders": False, "smart_trim": False, "delete_tool": False, "skills": False, "full_thinking": False, "no_planning_guard": False, "retry_cut_off": False, "text_tool_calls": False, "mcp": False, "plan_tool": False, "live_view": False, "script_check": False, "edit_checks": False, "stable_prompt": False, "announce_check": False, "session_cache": False, "ask_tool": False, "document_tool": False, "turn_guard": False, "user_tools": False, "tasks": False, "verify_on_finish": False, "backup_interval_h": 0}
    with open(CONFIG, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2)


def up():
    os.makedirs(WORKSPACE, exist_ok=True)
    write_config()
    docker("rm", "-f", LEGACY_CONTAINER)
    try:
        studio.ensure_sandbox(WS)
    except studio.ApiError as exc:
        sys.exit(exc.message)


def down():
    docker("rm", "-f", CONTAINER)


def reset():
    """Start over with an empty workspace. The old one is archived under runs/, never deleted."""
    down()
    if os.path.isdir(WORKSPACE) and os.listdir(WORKSPACE):
        os.makedirs(RUNS, exist_ok=True)
        target = os.path.join(RUNS, f"{stamp()}-workspace")
        os.rename(WORKSPACE, target)
        print(f"archived previous workspace to {target}")
    up()


def summarize(log):
    ran = re.findall(r"^Ran (\d+) tests?", log, re.M)
    if not ran:
        return None
    # Counts test methods, so a method with several failing subTests still counts once.
    failed = {m for m in re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", log, re.M)}
    return {"passed": max(0, int(ran[-1]) - len(failed)), "failed": len(failed), "failing": sorted(failed)}


def grade(project, record=True):
    """Run the project's checks in a fresh container. The project and grader are mounted read-only and
    the tests use a private copy, so nothing the project does can change either one."""
    name = f"{CONTAINER}-grade-{stamp()}"
    project = os.path.realpath(project)
    started = docker("run", "-d", "--name", name, *LOCKDOWN, "--tmpfs", "/tmp:exec,size=2g",
                     "-v", f"{project}:/work:ro", "-v", f"{GRADER}:/hidden:ro", "-e", "PROJECT=/tmp/project",
                     "-w", "/tmp", IMAGE, "sleep", "infinity")
    if started.returncode:
        sys.exit(f"could not start the grading container: {started.stderr.strip()}")
    steps, own, hidden = [], None, None

    def step(label, command, timeout=300):
        began = time.monotonic()
        try:
            result = docker("exec", "-w", "/tmp/project", name, "bash", "-c", f"timeout -s KILL {timeout} {command} 2>&1", timeout=timeout + 30)
            code, log = result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            code, log = 124, "[timed out]"
        steps.append({"name": label, "cmd": command, "state": "passed" if code == 0 else "failed", "exit": code,
                      "seconds": round(time.monotonic() - began, 1), "log": log[-20000:]})
        return log

    try:
        copied = docker("exec", name, "cp", "-a", "/work", "/tmp/project")
        if copied.returncode:
            sys.exit(f"could not copy the project: {copied.stderr.strip()}")
        step("layout", "bash -c 'test -f README.md && test -d tests && test -f tallybook/__main__.py'")
        step("compile", "python -m compileall -q .")
        own = summarize(step("own tests", "python -m unittest discover -s tests"))
        if own is None or own["passed"] + own["failed"] < MIN_OWN_TESTS:
            steps[-1]["state"] = "failed"
            steps[-1]["log"] += f"\n[arena] needs at least {MIN_OWN_TESTS} tests"
        hidden = summarize(step("hidden tests", "python /hidden/run_hidden.py", timeout=1200))
    finally:
        docker("rm", "-f", name)
    state = "passed" if all(s["state"] == "passed" for s in steps) else "failed"
    report = {"state": state, "project": project, "image": IMAGE, "steps": steps, "tests": own, "hidden_tests": hidden,
              "finished_at": time.time()}
    if record:
        folder = os.path.join(RUNS, stamp())
        shutil.copytree(project, os.path.join(folder, "files"), symlinks=True)
        with open(os.path.join(folder, "grade.json"), "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        report["folder"] = folder
    return report


def print_report(report):
    for item in report["steps"]:
        print(f"  {item['state']:<7} {item['name']:<13} exit {item['exit']:<4} {item['seconds']}s")
    for label, key in (("own tests", "tests"), ("hidden tests", "hidden_tests")):
        data = report[key]
        print(f"  {label}: " + (f"{data['passed']} passed, {data['failed']} failed" if data else "did not run"))
        for test in (data or {}).get("failing", [])[:60]:
            print(f"    - {test}")
    print(f"result: {report['state'].upper()}" + (f" (saved in {report['folder']})" if report.get("folder") else ""))


def selftest():
    """Prove the sandbox works: real escape attempts through Studio's own code paths, then the grader
    against the reference (must pass) and an empty project (must fail)."""
    probe = tempfile.mkdtemp(prefix=".selftest-", dir=HERE)
    results = []

    def check(label, ok, detail=""):
        results.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))

    ws = {"id": "selftest", "path": os.path.realpath(probe), "sandbox": True}
    run = types.SimpleNamespace(auto_commands=True, stop=False, ws=ws)

    def sh(command):
        return studio.tool_run(run, {"command": command})

    connect = "python -c \"import socket;socket.create_connection(('%s',%d),3)\""
    ok, _, out = sh("hostname; pwd; id -u")
    check("commands run in the container, in the folder", ok and "strata-sandbox" in out and "/work" in out, out)
    check("non-root user", str(os.getuid()) in out, out)
    ok, _, out = sh("touch made-inside && echo done")
    check("workspace is writable and shared with the host", ok and os.path.exists(os.path.join(probe, "made-inside")), out)
    ok, _, out = sh(f"ls {os.path.expanduser('~')} 2>&1; ls /home")
    check("host home directory is not visible", "Projects" not in out, out)
    check("Studio source is not visible", not sh(f"test -e {ROOT}/studio.py")[0])
    check("grader is not visible", not sh("test -e /hidden")[0] and not sh(f"test -e {GRADER}")[0])
    check("no internet", not sh(connect % ("1.1.1.1", 53))[0])
    check("gateway unreachable on loopback", not sh(connect % ("127.0.0.1", 8787))[0])
    check("gateway unreachable on docker bridge", not sh(connect % ("172.17.0.1", 8787))[0])
    ok, _, out = sh("grep CapEff /proc/self/status")
    check("no capabilities", ok and re.search(r"CapEff:\s+0+\s", out) is not None, out)
    ok, _, out = sh("sleep 60")
    check("timed-out command is killed", not ok and "timed out" in out, out)
    check("container recovers after a kill", sh("true")[0] and not sh("pgrep sleep || grep -lx sleep /proc/[0-9]*/comm | grep -v /1/")[0])
    terminal = studio.Terminal(ws, 80, 24)
    os.write(terminal.fd, b"echo TERM-$(hostname); exit\n")
    deadline = time.monotonic() + 15
    while b"TERM-strata-sandbox" not in bytes(terminal.buf) and time.monotonic() < deadline:
        time.sleep(0.1)
    check("terminals open in the container", b"TERM-strata-sandbox" in bytes(terminal.buf), bytes(terminal.buf)[-200:])
    terminal.proc.kill()
    real_folder = types.SimpleNamespace(auto_commands=True, stop=False, ws={**ws, "sandbox": False})
    ok, _, out = studio.tool_run(real_folder, {"command": f"test -e {ROOT}/studio.py && hostname"})
    check("with the sandbox off, commands run on the host", ok and "strata-sandbox" not in out, out)
    studio.remove_sandbox(ws)
    shutil.rmtree(probe, ignore_errors=True)
    shutil.rmtree(os.path.join(studio.APP.data_dir, "sandbox_homes", "selftest"), ignore_errors=True)

    with tempfile.TemporaryDirectory() as empty:
        os.chmod(empty, 0o755)
        print("grading an empty project (must fail):")
        report = grade(empty, record=False)
        print_report(report)
        check("empty project fails", report["state"] == "failed")
    print("grading the reference solution (must pass):")
    report = grade(REFERENCE, record=False)
    print_report(report)
    check("reference passes", report["state"] == "passed")
    print(f"selftest: {sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


def serve():
    up()
    os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "studio.py"), "--config", CONFIG])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["serve", "up", "down", "reset", "grade", "selftest"])
    command = parser.parse_args().command
    if command == "grade":
        report = grade(WORKSPACE)
        print_report(report)
        return 0 if report["state"] == "passed" else 1
    if command == "selftest":
        return selftest()
    {"serve": serve, "up": up, "down": down, "reset": reset}[command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
