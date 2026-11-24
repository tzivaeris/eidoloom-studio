"""Test 9: a self-contained Greek page. Only what a script can see; open the page to judge the wording."""
import re

from common import check, finish

try:
    with open("index.html", encoding="utf-8") as handle:
        page = handle.read()
except OSError as exc:
    check("index.html exists", False, repr(exc))
    finish()

check('declares lang="el"', re.search(r"<html[^>]*\blang=[\"']el", page, re.I))
check("declares UTF-8", re.search(r"charset=[\"']?utf-8", page, re.I))
check("no Chinese characters", not re.search(r"[一-鿿]", page))
check("no external resources", not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", page, re.I))
check("has a form with the four fields", all(re.search(rf"name=[\"']{field}[\"']", page) for field in ("name", "phone", "email", "date")))
visible = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
visible = re.sub(r"<[^>]+>", " ", visible)
check("visible text is Greek", len(re.findall(r"[Ͱ-Ͽἀ-῿]", visible)) > 3 * len(re.findall(r"[A-Za-z]", visible)),
      "too much Latin text")
script = " ".join(re.findall(r"<script\b.*?</script>", page, flags=re.S | re.I))
check("the phone rule is in the script", "69" in script)
check("error messages in the script are Greek", re.search(r"[Ͱ-Ͽ]", script))
finish()
