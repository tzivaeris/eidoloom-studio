"""Small statistics helpers."""


def mean(values):
    if not values:
        raise ValueError("mean of empty list")
    return sum(values) / len(values)


def median(values):
    if not values:
        raise ValueError("median of empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(values):
    """Population variance."""
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def clamp(value, low, high):
    return max(low, min(high, value))
