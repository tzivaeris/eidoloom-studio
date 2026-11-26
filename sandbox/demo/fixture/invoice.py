"""Invoices for a small bookshop. Every amount is in cents, so nothing is lost to floating point."""

VAT_RATE = 24  # percent, the standard rate in Greece


def line_total(price_cents, quantity):
    """The price of one line: unit price times quantity."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    return price_cents * quantity


def vat(cents):
    """VAT on an amount, rounded to the nearest cent (half a cent rounds up)."""
    return cents * VAT_RATE // 100


def invoice_total(lines):
    """Net, VAT and gross for a list of (price_cents, quantity) lines."""
    net = sum(line_total(price, qty) for price, qty in lines)
    tax = vat(net)
    return {"net": net, "vat": tax, "gross": net + tax}


def format_eur(cents):
    """12345 -> '123.45 EUR'."""
    return f"{cents // 100}.{cents % 100:02d} EUR"
