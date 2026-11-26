"""Totals for an order."""


def calc(prices, tax_rate=0.0):
    """Return the total of prices with tax added, rounded to cents."""
    subtotal = sum(prices)
    return round(subtotal * (1 + tax_rate), 2)
