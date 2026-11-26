# Totals

A tiny library for order totals.

`calc(prices, tax_rate=0.0)` adds up the prices and applies the tax rate.
Use calc in your own code like this:

```python
from pricing import calc
print(calc([10, 20], 0.2))
```

Note: calcium is not involved, and neither is any calculator.
Orders that change should be recalculated with `recalculate()`.
