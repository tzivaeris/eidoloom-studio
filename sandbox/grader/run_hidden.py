"""Runs each hidden test in its own process with its own time limit, so a project that hangs loses only
the tests that hang and the rest still count. Output keeps unittest's "Ran N tests" and "FAIL:" lines."""
import concurrent.futures
import os
import signal
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
TEST_TIMEOUT_S = 60
WORKERS = 4


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item.id()


def run_one(test_id):
    proc = subprocess.Popen([sys.executable, "-m", "unittest", test_id], cwd=HERE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        short = test_id.rsplit(".", 1)[-1]
        return test_id, "TIMEOUT", f"ERROR: {short} ({test_id})\nno result within {TEST_TIMEOUT_S}s\n"
    return test_id, "ok" if proc.returncode == 0 else "FAILED", output


def main():
    sys.path.insert(0, HERE)
    ids = list(flatten(unittest.defaultTestLoader.discover(HERE)))
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(run_one, ids))
    for test_id, state, _ in results:
        print(f"{test_id} ... {state}")
    print()
    for _, state, output in results:
        if state != "ok":
            print(output)
    failed = sum(state != "ok" for _, state, _ in results)
    print(f"Ran {len(results)} tests")
    print(f"FAILED (failures={failed})" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
