PRICES = {"apple": 50, "pear": 70, "plum": 90}


def handle(order):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")
    if not order.get("customer"):
        raise ValueError("customer is required")
    items = order.get("items")
    if not items:
        raise ValueError("order has no items")
    for item in items:
        if item.get("name") not in PRICES:
            raise ValueError("unknown item: %s" % item.get("name"))
        if not isinstance(item.get("qty"), int) or item["qty"] <= 0:
            raise ValueError("bad quantity for %s" % item["name"])
    total = sum(PRICES[i["name"]] * i["qty"] for i in items)
    if total > 1000:
        total = int(total * 0.9)
    return {"customer": order["customer"], "total": total, "lines": len(items)}
