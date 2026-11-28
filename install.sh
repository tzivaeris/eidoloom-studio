#!/usr/bin/env bash
# Install Eidoloom Studio on this machine, as the normal user (never root):
#
#   ./install.sh
#
# Studio itself needs only Python 3.10+ and systemd. This script checks them, makes the projects folder, and installs
# the eidoloom-studio user service (install-service.sh). Then open the address it prints, create the admin password,
# and install the tools from Account & settings → Tools & setup: one button for everything this machine can run.
#
# Not part of Studio, so not installed here: the model server (Ollama, llama.cpp, vLLM, LM Studio or the Strata gateway),
# which Studio reaches at "gateway" in studio.config.json (Ollama's http://127.0.0.1:11434 in the example).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -ne 0 ]] || die "run this as your normal user, not root. Studio runs as a systemd user service."
[[ -f "${DIR}/studio.py" ]] || die "studio.py is not next to this script; run it from Eidoloom Studio's folder."

command -v python3 >/dev/null 2>&1 || die "python3 is missing: sudo apt install python3"
python3 - <<'PY' || die "Eidoloom Studio needs Python 3.10 or newer."
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
log "Python: $(python3 --version 2>&1)"

service=1
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
  service=0      # containers, WSL without systemd, some servers: Studio itself runs fine, only the service is skipped
  log "No systemd user session here, so Studio is not installed as a service. Start it by hand: python3 ${DIR}/studio.py"
fi
mkdir -p "${HOME}/Projects"
log "Projects folder: ${HOME}/Projects (Studio opens folders inside it; change workspace_roots in studio.config.json for others)"

for tool in git docker node ffmpeg; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    log "Not here yet: ${tool}. Tools & setup lists what each tool needs and gives one sudo script for all of it."
  fi
done

if [[ ! -f "${DIR}/studio.config.json" && -f "${DIR}/studio.config.example.json" ]]; then
  cp "${DIR}/studio.config.example.json" "${DIR}/studio.config.json"
  log "Config: made studio.config.json from studio.config.example.json"
fi

if [[ -f "${DIR}/studio.config.json" ]]; then
  host="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("host", "127.0.0.1"))' "${DIR}/studio.config.json")"
  if [[ "${host}" == "127.0.0.1" ]] && ! command -v caddy >/dev/null 2>&1; then
    log "Note: studio.config.json binds Studio to 127.0.0.1, so only this machine can open it (or a web server in front of it, such as Caddy)."
    log "      For other computers on your network, set \"host\": \"0.0.0.0\" in studio.config.json and start Studio again."
  fi
fi

if [[ "${service}" == 1 ]]; then
  bash "${DIR}/install-service.sh"          # through bash: a ZIP download keeps no file modes
  restart="systemctl --user restart eidoloom-studio"
else
  restart="stop it (Ctrl+C) and start python3 ${DIR}/studio.py again"
fi

log ""
log "Next:"
log "  1. Open the address above (http://127.0.0.1:8790 unless you changed it) in a browser and create the admin password."
log "  2. Account & settings → Tools & setup: press 'Install everything this machine can run', or choose tools."
log "  3. The agent needs a model. Tools & setup → Local models installs PolyStrata or Strata with a model that fits this"
log "     machine, and Studio starts it itself. Or use a model server: Ollama on this machine works as it is. For llama.cpp, vLLM, LM Studio or another"
log "     address, set \"gateway\" (and \"gateway_key\" if it has one) in ${DIR}/studio.config.json, then"
log "     ${restart}. Or use Account & settings → Models, which finds the servers on this machine. README.md has the addresses."
