"""Test 20: validate(order) extracted from handle(); same messages, handle uses it, behaviour and tests unchanged."""
import filecmp
import subprocess
import sys

from common import check, finish

try:
    import orders
except Exception as exc:  # noqa: BLE001
    check("orders imports", False, repr(exc))
    finish()

good = {"customer": "ann", "items": [{"name": "apple", "qty": 2}]}
bad = [
    ("not a dict", [1], "order must be a dict"),
    ("no customer", {"items": good["items"]}, "customer is required"),
    ("no items", {"customer": "a", "items": []}, "order has no items"),
    ("unknown item", {"customer": "a", "items": [{"name": "kiwi", "qty": 1}]}, "unknown item: kiwi"),
    ("bad qty", {"customer": "a", "items": [{"name": "pear", "qty": 0}]}, "bad quantity for pear"),
    ("string qty", {"customer": "a", "items": [{"name": "pear", "qty": "2"}]}, "bad quantity for pear"),
]
check("validate exists and is callable", callable(getattr(orders, "validate", None)))
v = getattr(orders, "validate", None)
if callable(v):
    for label, order, msg in bad:
        try:
            v(order)
            check(f"validate rejects: {label}", False, "no error")
        except ValueError as exc:
            check(f"validate rejects: {label}", str(exc) == msg, f"message {str(exc)!r}")
        except Exception as exc:  # noqa: BLE001
            check(f"validate rejects: {label}", False, repr(exc))
    try:
        v(good)
        check("validate accepts a good order", True)
    except Exception as exc:  # noqa: BLE001
        check("validate accepts a good order", False, repr(exc))
    calls = []
    original = orders.validate
    orders.validate = lambda order: calls.append(order)
    try:
        orders.handle(good)
    except Exception:  # noqa: BLE001
        pass
    orders.validate = original
    check("handle() calls validate", len(calls) == 1)
    orders.validate = lambda order: (_ for _ in ()).throw(ValueError("stub says no"))
    try:
        orders.handle(good)
        check("handle() stops when validate raises", False, "no error")
    except ValueError as exc:
        check("handle() stops when validate raises", str(exc) == "stub says no")
    finally:
        orders.validate = original
for label, order, msg in bad:
    try:
        orders.handle(order)
        check(f"handle raises for: {label}", False)
    except ValueError as exc:
        check(f"handle raises for: {label}", str(exc) == msg, str(exc))
    except Exception as exc:  # noqa: BLE001
        check(f"handle raises for: {label}", False, repr(exc))
check("handle result, simple", orders.handle(good) == {"customer": "ann", "total": 100, "lines": 1})
check("handle result, discount", orders.handle({"customer": "bo", "items": [{"name": "plum", "qty": 20}]}) == {"customer": "bo", "total": 1620, "lines": 1})
check("handle result, two lines", orders.handle({"customer": "cy", "items": [{"name": "apple", "qty": 1}, {"name": "pear", "qty": 1}]}) == {"customer": "cy", "total": 120, "lines": 2})
check("tests unchanged", filecmp.cmp("tests/test_orders.py", "/fixture/tests/test_orders.py", shallow=False))
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the project's tests pass", t.returncode == 0, t.stderr[-300:])
finish()
