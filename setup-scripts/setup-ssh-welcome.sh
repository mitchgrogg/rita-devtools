#!/usr/bin/env bash
#
# Sets up an SSH login welcome message (MOTD).
#
# Usage: sudo bash setup-ssh-welcome.sh

set -e

GREEN='\033[0;32m'
RESET='\033[0m'

if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)." >&2
    exit 1
fi

# Create a dynamic MOTD script
cat > /etc/update-motd.d/99-rita-status <<'EOF'
#!/usr/bin/env bash

echo ""

# Print info about the always-on mitmproxy docker container
ETH0_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
if [ -n "$ETH0_IP" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^mitmproxy$'; then
  echo "  🔍  mitmproxy web UI: http://$ETH0_IP:8081"
  echo "  🔧  rita-mitm API:    http://$ETH0_IP:8082/api/config"
  echo ""
fi
EOF

chmod +x /etc/update-motd.d/99-rita-status

echo -e "${GREEN}SSH welcome message configured.${RESET}"
