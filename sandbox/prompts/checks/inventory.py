"""Test 12: reserve() checks and takes stock under two separate locks. Wrapping it in the same lock deadlocks."""
import inspect
import sys
import threading

from common import attempt, check, finish

try:
    import inventory
except Exception as exc:  # noqa: BLE001
    check("project imports", False, repr(exc))
    finish()


def sequential():
    stock = inventory.Stock(3)
    return [stock.reserve(2), stock.reserve(2), stock.reserve(1), stock.reserve(1), stock.available()]


done = {}
worker = threading.Thread(target=lambda: done.update(result=sequential()), daemon=True)
worker.start()
worker.join(5)
check("no deadlock", not worker.is_alive())
if worker.is_alive():
    finish()
attempt("plain reservations are right", lambda: done["result"], [True, False, True, False, 0])


def stress():
    sys.setswitchinterval(1e-6)
    bad = 0
    for _ in range(300):
        stock = inventory.Stock(5)
        results = []
        barrier = threading.Barrier(16)

        def buy():
            barrier.wait()
            results.append(stock.reserve(1))

        threads = [threading.Thread(target=buy, daemon=True) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        if any(thread.is_alive() for thread in threads):
            return "hang"
        if results.count(True) != 5 or stock.available() != 0:
            bad += 1
    return bad


attempt("300 rounds of 16 buyers never oversell", stress, 0)
source = inspect.getsource(inventory)
check("no sleeps or retries were added", "sleep" not in source and "retry" not in source.lower())
with open("tests/test_inventory.py", encoding="utf-8") as handle:
    text = handle.read()
check("the concurrency test is still there", "test_no_overselling" in text and "assertEqual(results.count(True), 5)" in text)
finish()
