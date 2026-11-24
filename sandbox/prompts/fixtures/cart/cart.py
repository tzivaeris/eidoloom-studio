from dataclasses import dataclass, field


def apply_discount(price_cents, pct):
    """Price after a percentage discount, in whole cents, with halves rounded up."""
    return (2 * price_cents * (100 - pct) + 100) // 200


@dataclass
class Item:
    name: str
    price_cents: int
    qty: int = 1


@dataclass
class Cart:
    items: list = field(default_factory=list)
    discount_pct: int = 0

    def add(self, name, price_cents, qty=1):
        self.items.append(Item(name, price_cents, qty))

    def subtotal(self):
        return sum(item.price_cents * item.qty for item in self.items)

    def total(self):
        for item in self.items:
            item.price_cents = apply_discount(item.price_cents, self.discount_pct)
        return self.subtotal()
