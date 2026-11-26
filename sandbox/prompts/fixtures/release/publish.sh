#!/bin/sh
# Builds the release tarball and uploads it to the release server.
set -e
cd "$(dirname "$0")"
NAME="app-$(cat VERSION).tgz"
SERVER="http://127.0.0.1:9/upload"

tar czf "/tmp/$NAME" app.py VERSION
echo "Built /tmp/$NAME"

if command -v curl >/dev/null 2>&1; then
    curl -sS --fail -X PUT --data-binary "@/tmp/$NAME" "$SERVER/$NAME"
else
    python3 - "/tmp/$NAME" "$SERVER/$NAME" <<'PY'
import sys
import urllib.request

path, url = sys.argv[1], sys.argv[2]
with open(path, "rb") as handle:
    request = urllib.request.Request(url, data=handle.read(), method="PUT")
urllib.request.urlopen(request, timeout=10)
PY
fi

echo "Published at https://releases.example.com/$NAME"
