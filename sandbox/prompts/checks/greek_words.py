"""Test 6: words.py, whose function name the prompt leaves open. Tries each public function on Greek text."""
import inspect

from common import check, finish

try:
    import words
except Exception as exc:  # noqa: BLE001
    check("words.py imports", False, repr(exc))
    finish()

text = "Η Μαρία, ο Γιώργος! Καλημέρα; «Ελλάδα»"
results = []
for name, fn in inspect.getmembers(words, inspect.isfunction):
    if name.startswith("_") or fn.__module__ != "words":
        continue
    try:
        results.append(fn(text))
    except Exception:  # noqa: BLE001 - other helpers may take other arguments
        pass
counted = [r for r in results if isinstance(r, (dict, tuple, list)) and "6" in repr(r)]
longest = [r for r in results if "Καλημέρα" in repr(r)]
check("counts 6 Greek words", counted or any(r == 6 for r in results), results)
check("longest word is Καλημέρα, with no punctuation", longest and all("Καλημέρα;" not in repr(r) for r in longest), results)
finish()
