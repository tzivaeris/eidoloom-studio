"""Test 22: deploy.sh gets --dry-run; without it the script does what it did."""
import os
import shutil
import subprocess
import tempfile

from common import check, finish

syn = subprocess.run(["bash", "-n", "deploy.sh"], capture_output=True, text=True)
check("bash -n deploy.sh passes", syn.returncode == 0, syn.stderr)


def tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read() if not f.endswith(".tgz") else b"tgz"
    return out


def tgz_names(root):
    import tarfile
    p = os.path.join(root, "release.tgz")
    if not os.path.exists(p):
        return None
    with tarfile.open(p) as t:
        return sorted(n.lstrip("./") for n in t.getnames() if n.lstrip("./"))


def sandbox(script_dir):
    d = tempfile.mkdtemp()
    shutil.copytree(script_dir, d, dirs_exist_ok=True)
    return d


def run(d, *args):
    return subprocess.run(["bash", "deploy.sh", *args], cwd=d, capture_output=True, text=True, timeout=30)


dry = sandbox(".")
r = run(dry, "--dry-run")
check("--dry-run exits 0", r.returncode == 0, r.stderr)
check("--dry-run creates no build/", not os.path.exists(os.path.join(dry, "build")))
check("--dry-run creates no release.tgz", not os.path.exists(os.path.join(dry, "release.tgz")))
plus = [l for l in r.stdout.splitlines() if l.startswith("+ ")]
for word in ("mkdir", "cp", "tar"):
    check(f'a "+ " line for {word}', any(word in l for l in plus), r.stdout)
check("echo is also shown or at least nothing is run", len(plus) >= 3)
check("--dry-run does not print the real 'deployed' line on its own", "deployed" not in [l.strip() for l in r.stdout.splitlines()])

mine = sandbox(".")
ref = sandbox("/fixture")
rm = run(mine)
rr = run(ref)
check("plain run exits 0", rm.returncode == 0, rm.stderr)
check("plain run prints the same output as the original", rm.stdout == rr.stdout, f"{rm.stdout!r} vs {rr.stdout!r}")
check("plain run creates the same files", tree(mine).keys() - {"deploy.sh"} == tree(ref).keys() - {"deploy.sh"})
check("plain run builds the same archive", tgz_names(mine) == tgz_names(ref))
finish()
