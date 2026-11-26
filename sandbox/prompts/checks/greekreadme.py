"""Test 34: a Greek README.md whose examples really run."""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from common import check, finish
from greekcheck import greek_share, has_cjk, mixed_words

if not os.path.isfile("README.md"):
    check("README.md exists", False)
    finish()
text = open("README.md", encoding="utf-8").read()
check("README.md exists and has content", len(text.strip()) > 200, f"{len(text.strip())} characters")
share = greek_share(text)
check("the prose is mostly Greek", share > 0.6, f"Greek share {share:.2f}")
check("no Chinese, Japanese or Korean characters", not has_cjk(text))
check("no words mixing Greek and Latin letters", not mixed_words(text), str(mixed_words(text)[:5]))
code = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S))
for command in ("add", "list", "search", "remove"):
    check(f"the command {command} is mentioned", re.search(rf"\b{command}\b", text) is not None)
lines = []
for raw in code.splitlines():
    line = re.sub(r"^\s*[$>]\s*", "", raw).strip()
    # A usage line with placeholders («notes.py <εντολή> [ορίσματα]») is not an example to run.
    if re.match(r"python3?\s+notes\.py\b", line) and not re.search(r"<[^>]+>", line):
        lines.append(line)
check("the README has runnable examples", len(lines) >= 4, f"{len(lines)} example lines")
used = {c for line in lines for c in ("add", "list", "search", "remove") if re.search(rf"notes\.py\s+{c}\b", line)}
check("every command has an example", used == {"add", "list", "search", "remove"}, f"missing {sorted({'add','list','search','remove'} - used)}")
work = tempfile.mkdtemp()
shutil.copy("notes.py", work)
bad, worked = [], set()
for line in lines:
    try:
        args = shlex.split(line)[1:]
    except ValueError as exc:
        bad.append(f"{line} ({exc})")
        continue
    if ">" in args or "|" in args:
        args = args[: max(args.index(t) for t in (">", "|") if t in args)]
    r = subprocess.run([sys.executable, *args], cwd=work, capture_output=True, text=True, timeout=30)
    if "Traceback" in r.stderr:
        bad.append(f"{line} -> a Python traceback")
    elif r.returncode == 0:
        worked |= {c for c in ("add", "list", "search", "remove") if re.search(rf"notes\.py\s+{c}\b", line)}
# An example may show an error on purpose («remove 99» for a note that is not there, or the usage message), as long as
# the program answers it cleanly; every command still needs one example that works.
check("no example crashes with a traceback", not bad, "; ".join(bad[:3]))
check("every command has an example that runs with exit 0, in order", worked == {"add", "list", "search", "remove"},
      f"no working example of {sorted({'add', 'list', 'search', 'remove'} - worked)}")
shutil.rmtree(work, ignore_errors=True)
finish()
