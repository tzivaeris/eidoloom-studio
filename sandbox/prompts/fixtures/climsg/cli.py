"""A tiny to-do list on the command line. Items are kept in items.json in the current folder."""
import argparse
import json
import os
import sys
import time

STORE = "items.json"


def load():
    if not os.path.exists(STORE):
        return []
    with open(STORE, encoding="utf-8") as handle:
        return json.load(handle)


def save(items):
    with open(STORE, "w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2)


def cmd_add(args):
    name = args.name.strip()
    if not name:
        print("Error: the name must not be empty.", file=sys.stderr)
        return 2
    items = load()
    if any(item["name"] == name for item in items):
        print(f"Error: an item named '{name}' already exists.", file=sys.stderr)
        return 1
    new_id = max([item["id"] for item in items], default=0) + 1
    items.append({"id": new_id, "name": name, "created": time.strftime("%Y-%m-%d")})
    save(items)
    print(f"Added item {new_id}: {name}")
    return 0


def cmd_list(args):
    items = load()
    if args.json:
        print(json.dumps(items))
        return 0
    if not items:
        print("There are no items yet.")
        return 0
    for item in items:
        print(f"{item['id']}  {item['name']}  ({item['created']})")
    return 0


def cmd_remove(args):
    items = load()
    kept = [item for item in items if item["id"] != args.id]
    if len(kept) == len(items):
        print(f"Error: there is no item with id {args.id}.", file=sys.stderr)
        return 1
    save(kept)
    print(f"Removed item {args.id}.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="cli.py", description="Keep a simple list of items.")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Add a new item to the list.")
    add.add_argument("name", help="The name of the item to add.")
    add.set_defaults(func=cmd_add)
    lst = sub.add_parser("list", help="Show all items.")
    lst.add_argument("--json", action="store_true", help="Print the items as JSON.")
    lst.set_defaults(func=cmd_list)
    rem = sub.add_parser("remove", help="Remove an item by its id.")
    rem.add_argument("id", type=int, help="The id of the item to remove.")
    rem.set_defaults(func=cmd_remove)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
