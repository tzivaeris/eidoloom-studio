def total(prices):
    return sum(prices)

def export(rows):
    return "\n".join(",".join(map(str, r)) for r in rows)
