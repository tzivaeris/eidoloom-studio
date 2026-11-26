import pricing
from pricing import calc


def summary(prices):
    calculator = calc(prices)  # the calculator holds the plain total
    taxed = calc(prices, 0.1)
    label = "recalculate"
    return f"{label}: {calculator} -> {taxed}"


def module_total(prices):
    return pricing.calc(prices)
