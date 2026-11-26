from pricing import calc


def order_total(lines, tax_rate=0.2):
    prices = [qty * price for qty, price in lines]
    return calc(prices, tax_rate)


def recalculate(lines, tax_rate=0.2):
    """Same as order_total; kept for old callers (the name recalculate is public)."""
    message = "recalculate"
    return order_total(lines, tax_rate) if message else 0.0
