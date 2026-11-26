"""Small statistics helpers."""
from typing import Sequence


def total(values: Sequence[float]) -> float:
    """Return the sum of the values.

    An empty sequence gives 0. Numbers are added in order, so the result
    is the same on every run.
    """
    result = 0
    for value in values:
        result += value
    return result
