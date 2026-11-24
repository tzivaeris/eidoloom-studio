#!/usr/bin/env bash
# Install, update or remove the Eidoloom Studio systemd user service.
#
#   ./install-service.sh              install or update the unit, start it, enable lingering
#   ./install-service.sh --uninstall  stop and disable the service, remove the unit file
#
# Run as the normal server user (the account you sign in with), never as root.
set -euo pipefail

UNIT_NAME="strata-studio.service"
DEFAULT_PORT=8790
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_UNIT="${SCRIPT_DIR}/${UNIT_NAME}"
UNIT_DIR="${HOME}/.config/systemd/user"
DEST_UNIT="${UNIT_DIR}/${UNIT_NAME}"
PROJECT_DIR="${SCRIPT_DIR}"          # wherever this folder is on this machine
CONFIG_FILE="${PROJECT_DIR}/studio.config.json"

log() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: ./install-service.sh [--uninstall]

  (no option)   install or update ${UNIT_NAME}, start it, enable lingering
  --uninstall   stop and disable ${UNIT_NAME}, remove ${DEST_UNIT}
EOF
}

if [[ "${EUID}" -eq 0 ]]; then
  die "do not run this script as root. Run it as the normal user that owns the Eidoloom Studio folder (the account you sign in with). The service is a systemd user unit and belongs to that user."
fi

USER="$(id -un)"

check_user_manager() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found. This script needs systemd."
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    die "cannot reach the systemd user manager for ${USER}. Log in as ${USER} with a normal session (ssh or console). Do not use sudo -u to run this script."
  fi
}

read_port() {
  local port="${DEFAULT_PORT}"
  if [[ -f "${CONFIG_FILE}" ]]; then
    port="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("port", 8790))' "${CONFIG_FILE}" 2>/dev/null || echo "${DEFAULT_PORT}")"
  fi
  printf '%s' "${port}"
}

install_service() {
  command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH."
  [[ -f "${SRC_UNIT}" ]] || die "unit file not found next to this script: ${SRC_UNIT}"
  check_user_manager

  if [[ ! -f "${PROJECT_DIR}/studio.py" ]]; then
    warn "${PROJECT_DIR}/studio.py does not exist yet. The service will keep restarting until it is in place."
  fi

  mkdir -p "${UNIT_DIR}"
  # The unit names %h/Projects/Tools/StrataStudio; a copy of Studio elsewhere gets its own folder written in.
  local rendered
  rendered="$(mktemp)"
  if [[ "${PROJECT_DIR}" == "${HOME}/Projects/Tools/StrataStudio" ]]; then
    cp "${SRC_UNIT}" "${rendered}"
  else
    sed "s|%h/Projects/Tools/StrataStudio|${PROJECT_DIR}|g" "${SRC_UNIT}" > "${rendered}"
  fi
  SRC_UNIT="${rendered}"
  local unit_changed=0
  if [[ -f "${DEST_UNIT}" ]] && cmp -s "${SRC_UNIT}" "${DEST_UNIT}"; then
    log "Unit file already up to date: ${DEST_UNIT}"
  else
    cp -f "${SRC_UNIT}" "${DEST_UNIT}"
    chmod 644 "${DEST_UNIT}"
    unit_changed=1
    log "Installed unit file: ${DEST_UNIT}"
  fi

  local was_active=0
  if systemctl --user is-active --quiet "${UNIT_NAME}"; then
    was_active=1
  fi

  systemctl --user daemon-reload
  systemctl --user enable --now "${UNIT_NAME}"

  if [[ "${unit_changed}" -eq 1 && "${was_active}" -eq 1 ]]; then
    systemctl --user restart "${UNIT_NAME}"
    log "Restarted the service to load the new unit file."
  fi

  local linger
  linger="$(loginctl show-user "${USER}" -p Linger 2>/dev/null | cut -d= -f2 || true)"
  if [[ "${linger}" == "yes" ]]; then
    log "Lingering is already enabled for ${USER}."
  elif loginctl enable-linger "${USER}"; then
    log "Enabled lingering for ${USER} (the service runs at boot and after logout)."
  else
    warn "could not enable lingering for ${USER}. The service will stop when the last session of ${USER} ends. An administrator can run: sudo loginctl enable-linger ${USER}"
  fi

  log ""
  systemctl --user --no-pager status "${UNIT_NAME}" || true

  local ip port
  ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  ip="${ip:-localhost}"
  port="$(read_port)"
  log ""
  log "Eidoloom Studio: http://${ip}:${port}/"
  log "Logs: journalctl --user -u ${UNIT_NAME} -f"
}

uninstall_service() {
  check_user_manager

  if systemctl --user disable --now "${UNIT_NAME}" >/dev/null 2>&1; then
    log "Stopped and disabled ${UNIT_NAME}."
  else
    log "${UNIT_NAME} was not enabled or running."
  fi

  if [[ -f "${DEST_UNIT}" ]]; then
    rm -f "${DEST_UNIT}"
    log "Removed ${DEST_UNIT}."
  else
    log "No unit file at ${DEST_UNIT}."
  fi

  systemctl --user daemon-reload
  log "Done. Lingering was left unchanged. To turn it off: loginctl disable-linger ${USER}"
}

case "${1:-}" in
  "")            install_service ;;
  --uninstall)   uninstall_service ;;
  -h|--help)     usage ;;
  *)             usage >&2; die "unknown option: $1" ;;
esac
