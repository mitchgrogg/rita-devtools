#!/usr/bin/env bash

set -e

GREEN='\033[0;32m'
RESET='\033[0m'

if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)." >&2
    exit 1
fi

if [ -z "$1" ]; then
  echo "Usage: $0 <mitmproxy-web-password>"
  echo "Error: mitmproxy web password is required."
  exit 1
fi

MITMPROXY_PASSWORD="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR="$(cd "$SCRIPT_DIR/../rita-mitm" && pwd)"

# Check for an existing container so re-running this script with different args
# (e.g. new password) will remove the existing container and start it with the
# new options
if docker ps -a --format '{{.Names}}' | grep -q '^mitmproxy$'; then
  echo "Removing existing mitmproxy container..."
  docker rm -f mitmproxy
fi

echo "Starting mitmproxy container..."
docker run -d \
  --restart unless-stopped \
  --name mitmproxy \
  -p 8080:8080 \
  -p 8081:8081 \
  -p 8082:8082 \
  -v "$ADDON_DIR":/opt/rita-mitm:ro \
  -v mitmproxy-data:/data \
  mitmproxy/mitmproxy \
  mitmweb \
  --web-host 0.0.0.0 \
  --set web_password="$MITMPROXY_PASSWORD" \
  -s /opt/rita-mitm/addon.py

ETH0_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
echo ""
echo -e "${GREEN}mitmproxy is running."
echo "  Proxy:         $ETH0_IP:8080"
echo "  Web interface: http://$ETH0_IP:8081"
echo "  Addon API:     http://$ETH0_IP:8082"
echo -e "  Web password:  $MITMPROXY_PASSWORD${RESET}"
