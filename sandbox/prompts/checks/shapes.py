"""Test 30: type hints on shapes.py only; behaviour and other files unchanged."""
import ast
import filecmp
import math
import os
import subprocess
import sys

from common import attempt, check, finish

tree = ast.parse(open("shapes.py", encoding="utf-8").read())
missing, seen = [], 0
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    seen += 1
    a = node.args
    params = a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])
    for i, p in enumerate(params):
        if p.arg in ("self", "cls") and i == 0 and node.name not in ("area_circle",):
            continue
        if p.annotation is None:
            missing.append(f"{node.name}({p.arg})")
    if node.returns is None and node.name != "__init__":
        missing.append(f"{node.name} -> ?")
check("every parameter and return value is annotated", not missing, ", ".join(missing[:6]))
check("no function was removed", seen >= 15, f"{seen} functions")
import shapes  # noqa: E402

r, c = shapes.Rectangle.from_corners(0, 0, 3, 3), shapes.Circle.unit().scaled(2)
attempt("functions behave as before", lambda: [shapes.area_circle(2) == 4 * math.pi, shapes.perimeter_rect(2, 3), shapes.scale_point((1, 2), 3), shapes.scale_point((1, 2)), shapes.bounding_box([(1, 5), (3, 2), (0, 4)])], [True, 10, (3, 6), (1.0, 2.0), (0, 2, 3, 5)])
attempt("classes behave as before", lambda: [r.is_square, r.area(), shapes.Rectangle(2, 5, "box").name, shapes.describe(r), shapes.describe(c, 1), c.diameter, shapes.Circle.unit().radius, shapes.total_area([r, shapes.Rectangle(1, 2)])], [True, 9, "box", "rectangle: area 9", "circle: area 12.6", 4, 1, 11])
check("is_square and diameter are still properties", isinstance(shapes.Rectangle.__dict__["is_square"], property) and isinstance(shapes.Circle.__dict__["diameter"], property))
check("from_corners and unit are still static", isinstance(shapes.Rectangle.__dict__["from_corners"], staticmethod) and isinstance(shapes.Circle.__dict__["unit"], staticmethod))


def listing(root):
    out = set()
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        out.update(os.path.relpath(os.path.join(base, n), root) for n in names if not n.endswith(".pyc"))
    return out


mine, orig = listing("."), listing("/fixture")
check("no files added or removed", mine == orig, str(sorted(mine ^ orig)))
changed = [f for f in orig & mine if f != "shapes.py" and not filecmp.cmp(f, os.path.join("/fixture", f), shallow=False)]
check("other files are unchanged", not changed, str(changed))
t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=60)
check("the tests pass", t.returncode == 0, t.stderr[-300:])
finish()
