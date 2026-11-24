"""Test 11: the discount is applied more than once because Cart.total() changes the items."""
import copy
import subprocess
import sys

from common import attempt, check, finish

try:
    import cart as cart_module
    import report
except Exception as exc:  # noqa: BLE001
    check("project imports", False, repr(exc))
    finish()


def make(pct=10):
    cart = cart_module.Cart(discount_pct=pct)
    cart.add("Book", 10000)
    cart.add("Pen", 250, 2)
    return cart


attempt("apply_discount is unchanged", lambda: [cart_module.apply_discount(10000, 10), cart_module.apply_discount(150, 50), cart_module.apply_discount(999, 0)], [9000, 75, 999])


def repeated():
    cart = make()
    return [cart.total(), cart.total(), cart.total()]


attempt("total() is the same every time", repeated, [9450, 9450, 9450])


def items_unchanged():
    cart = make()
    before = copy.deepcopy(cart.items)
    cart.total()
    return cart.items == before


attempt("total() leaves the items' prices alone", items_unchanged, True)


def subtotal_after():
    cart = make()
    cart.total()
    return cart.subtotal()


attempt("subtotal() is still undiscounted afterwards", subtotal_after, 10500)


def receipt_lines():
    return report.receipt(make()).splitlines()[-2:]


attempt("receipt shows the right total", receipt_lines, ["Total: 94.50", "Paid in full: 94.50"])


def receipt_twice():
    cart = make()
    first = report.receipt(cart)
    return first == report.receipt(cart)


attempt("printing a receipt twice gives the same receipt", receipt_twice, True)
tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the project's tests pass", tests.returncode == 0, tests.stderr[-300:])
with open("tests/test_cart.py", encoding="utf-8") as handle:
    check("test_receipt still expects 90.00", '"Paid in full: 90.00"' in handle.read())
finish()
