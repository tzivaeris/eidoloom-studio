"""Test 19: dark_mode false added to features, max_upload_mb is 50, everything else identical."""
import json

from common import check, finish

try:
    new = json.load(open("config/settings.json", encoding="utf-8"))
    old = json.load(open("/fixture/config/settings.json", encoding="utf-8"))
    check("settings.json is valid JSON", True)
except Exception as exc:  # noqa: BLE001
    check("settings.json is valid JSON", False, repr(exc))
    finish()


def find(obj, key, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append((path + "/" + k, v))
            out += find(v, key, path + "/" + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += find(v, key, f"{path}[{i}]")
    return out


check("features.dark_mode is false", isinstance(new.get("features"), dict) and new["features"].get("dark_mode") is False)
found = find(new, "max_upload_mb")
check("max_upload_mb is 50 (one key, as an integer)", len(found) == 1 and found[0][1] == 50 and type(found[0][1]) is int, found)
exp = json.loads(json.dumps(old))
exp["features"]["dark_mode"] = False
for path, _ in find(exp, "max_upload_mb"):
    exp["limits"]["max_upload_mb"] = 50
check("everything else is unchanged", new == exp)
finish()
