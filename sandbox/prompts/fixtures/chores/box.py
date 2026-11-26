"""A box that holds a limited number of items."""

MAX_ITEMS = 5


def new_box():
    return []


def add_item(box, item):
    """Add an item; returns the message shown to the user."""
    if len(box) >= MAX_ITEMS:
        raise ValueError(f"box is full (max {MAX_ITEMS})")
    box.append(item)
    return f"Recieved {item!r} ({len(box)}/{MAX_ITEMS})"


def remove_item(box, item):
    box.remove(item)
    return box
