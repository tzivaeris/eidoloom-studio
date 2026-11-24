"""Test 14: every function added since the Tallybook baseline has a Greek docstring, and the features exist."""
import ast
import os
import re

from common import check, finish


def functions(root):
    found = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        key = (os.path.relpath(path, root), node.name)
                        found[key] = ast.get_docstring(node) or ""
    return found


before, after = functions("/baseline"), functions(os.getcwd())
new = {key: doc for key, doc in after.items() if key not in before}
check("new functions were added", len(new) >= 3, len(new))
missing = [f"{path}:{name}" for (path, name), doc in new.items() if not re.search(r"[Ͱ-Ͽ]", doc)]
check("every new function has a Greek docstring", not missing, f"{len(missing)} of {len(new)} missing: {missing[:8]}")
text = ""
for folder, dirs, names in os.walk("tallybook"):
    for name in names:
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                text += handle.read()
check("the balance route exists", "balance" in text)
check("the clients command exists", re.search(r"""add_parser\(\s*["']clients["']""", text))
check("no Chinese characters in the code", not re.search(r"[一-鿿]", text))
finish()
