"""Test 10: accent-insensitive Greek search and sorting."""
import re

from common import attempt, check, finish

try:
    import greektext
except Exception as exc:  # noqa: BLE001
    check("greektext.py imports", False, repr(exc))
    finish()

attempt("normalize: accents and final sigma", lambda: greektext.normalize("Γιώργος"), "γιωργοσ")
attempt("normalize: capitals with no accent", lambda: greektext.normalize("ΓΙΩΡΓΟΣ"), "γιωργοσ")
attempt("normalize: dialytika", lambda: greektext.normalize("Ταΐζω"), "ταιζω")
attempt("normalize: dialytika with tonos", lambda: greektext.normalize("ΐ ΰ"), "ι υ")
attempt("normalize: Latin text is kept", lambda: greektext.normalize("Python Ά"), "python α")
names = ["Γιώργος", "ΓΙΏΡΓΟΣ", "Μαρία", "Ελένη"]
attempt("search: no accents finds accented", lambda: greektext.search("γιωργος", names), ["Γιώργος", "ΓΙΏΡΓΟΣ"])
attempt("search: capitals find lower case", lambda: greektext.search("ΜΑΡΙΑ", names), ["Μαρία"])
attempt("search: part of a word", lambda: greektext.search("ωργ", names), ["Γιώργος", "ΓΙΏΡΓΟΣ"])
attempt("search: final sigma in the middle of a query", lambda: greektext.search("γιωργος", ["ο Γιώργος Παπάς"]), ["ο Γιώργος Παπάς"])
attempt("search: nothing found", lambda: greektext.search("νίκος", names), [])
attempt("sort: Greek alphabetical order, ignoring accents and case", lambda: greektext.sort_names(["Ωρίων", "Άννα", "βασίλης", "Αλέξης", "Έλενα", "Ελένη"]),
        ["Αλέξης", "Άννα", "βασίλης", "Έλενα", "Ελένη", "Ωρίων"])
attempt("sort: the input list is not changed", lambda: (lambda data: (greektext.sort_names(data), data)[1])(["β", "α"]), ["β", "α"])
with open("greektext.py", encoding="utf-8") as handle:
    check("no Chinese characters in the code", not re.search(r"[一-鿿]", handle.read()))
finish()
