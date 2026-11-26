"""A small notes keeper. Notes are stored in notes.json in the current folder."""
import json
import os
import sys
import time

STORE = "notes.json"


def load():
    if not os.path.exists(STORE):
        return []
    with open(STORE, encoding="utf-8") as handle:
        return json.load(handle)


def save(notes):
    with open(STORE, "w", encoding="utf-8") as handle:
        json.dump(notes, handle, indent=2, ensure_ascii=False)


def add(text):
    notes = load()
    note = {"id": max([n["id"] for n in notes], default=0) + 1, "text": text, "created": time.strftime("%Y-%m-%d")}
    notes.append(note)
    save(notes)
    return note


def remove(note_id):
    notes = load()
    kept = [n for n in notes if n["id"] != note_id]
    save(kept)
    return len(kept) != len(notes)


def search(word):
    return [n for n in load() if word.lower() in n["text"].lower()]


def show(notes):
    for n in notes:
        print(f"{n['id']}: {n['text']} ({n['created']})")


def main(argv):
    if not argv:
        print("usage: notes.py add TEXT | list | search WORD | remove ID")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "add" and rest:
        note = add(" ".join(rest))
        print(f"added {note['id']}")
        return 0
    if cmd == "list":
        show(load())
        return 0
    if cmd == "search" and rest:
        show(search(rest[0]))
        return 0
    if cmd == "remove" and rest and rest[0].isdigit():
        if remove(int(rest[0])):
            print("removed")
            return 0
        print("no such note")
        return 1
    print("usage: notes.py add TEXT | list | search WORD | remove ID")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
