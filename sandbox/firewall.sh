#!/usr/bin/env bash
# Stops Studio's sandbox containers from reaching this machine and the local network. Internet still works.
# Needs root. Run:  sudo bash sandbox/firewall.sh apply | remove | status | install-boot | remove-boot
#
# Two sets of rules are needed:
#   INPUT        traffic to this machine's own addresses (services such as next dev on :5000)
#   DOCKER-USER  traffic Docker forwards (other containers' published ports such as :9000, and the LAN)
# DNS (port 53) to private addresses stays open, because containers use the router as their DNS server.
set -euo pipefail

BRIDGE=strata-sbx0
PRIVATE=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10)
UNIT=/etc/systemd/system/strata-sandbox-firewall.service
TAG="strata-sandbox"

remove_rules() {
  local chain rule
  for chain in INPUT DOCKER-USER; do
    # grep finds nothing when there are no rules yet, which is fine.
    { iptables -S "$chain" 2>/dev/null || true; } | { grep -F -- "--comment $TAG" || true; } | sed 's/^-A/-D/' | while read -r rule; do
      eval "iptables $rule"
    done
  done
}

add() { iptables "$@" -m comment --comment "$TAG"; }

apply_rules() {
  iptables -N DOCKER-USER 2>/dev/null || true     # exists once Docker has started
  remove_rules
  # -I puts each rule first, so these are added in reverse of the order they apply.
  add -I INPUT -i "$BRIDGE" -j DROP
  add -I INPUT -i "$BRIDGE" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  local net
  for net in "${PRIVATE[@]}"; do
    add -I DOCKER-USER -i "$BRIDGE" -d "$net" -j DROP
  done
  add -I DOCKER-USER -i "$BRIDGE" -p tcp --dport 53 -j RETURN
  add -I DOCKER-USER -i "$BRIDGE" -p udp --dport 53 -j RETURN
  add -I DOCKER-USER -i "$BRIDGE" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
}

status() {
  local chain
  for chain in INPUT DOCKER-USER; do
    echo "$chain:"
    iptables -S "$chain" 2>/dev/null | grep -F -- "--comment $TAG" || echo "  (no sandbox rules)"
  done
}

case "${1:-}" in
  apply) apply_rules; status ;;
  remove) remove_rules; status ;;
  status) status ;;
  install-boot)
    # Rules are lost on reboot. This reapplies them once Docker is up.
    cat > "$UNIT" <<EOF
[Unit]
Description=Eidoloom Studio sandbox firewall rules
After=docker.service ufw.service
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/bash $(realpath "$0") apply

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now strata-sandbox-firewall.service
    ;;
  remove-boot)
    systemctl disable --now strata-sandbox-firewall.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    ;;
  *) echo "usage: $0 apply|remove|status|install-boot|remove-boot" >&2; exit 2 ;;
esac
