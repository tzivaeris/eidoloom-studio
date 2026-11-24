def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def receipt(cart):
    lines = [f"{item.qty} x {item.name} @ {money(item.price_cents)}" for item in cart.items]
    lines.append(f"Total: {money(cart.total())}")
    lines.append(f"Paid in full: {money(cart.total())}")
    return "\n".join(lines)
