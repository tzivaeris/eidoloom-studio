"""Test 21: requests>=2.31 added, stats script added, nothing else changed in pyproject.toml."""
import copy
import re
import tomllib

from common import check, finish

try:
    new = tomllib.load(open("pyproject.toml", "rb"))
    old = tomllib.load(open("/fixture/pyproject.toml", "rb"))
    check("pyproject.toml parses", True)
except Exception as exc:  # noqa: BLE001
    check("pyproject.toml parses", False, repr(exc))
    finish()

deps = new.get("project", {}).get("dependencies", [])
norm = [re.sub(r"\s+", "", d).lower() for d in deps]
check("requests>=2.31 is a dependency", "requests>=2.31" in norm, deps)
check("click>=8.1 is still a dependency", "click>=8.1" in norm, deps)
check("no other dependencies", len(deps) == 2, deps)
scripts = new.get("project", {}).get("scripts", {})
check('[project.scripts] stats = "stats.cli:main"', scripts.get("stats") == "stats.cli:main", scripts)
exp = copy.deepcopy(old)
got = copy.deepcopy(new)
exp["project"].pop("dependencies")
got["project"].pop("dependencies", None)
got["project"].pop("scripts", None)
check("everything else is unchanged", got == exp)
finish()
