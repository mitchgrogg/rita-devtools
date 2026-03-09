#!/usr/bin/env bash

set -e

if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)." >&2
    exit 1
fi

if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
  echo "Usage: $0 <hotspot-ssid> <hotspot-password> <mitmproxy-web-password>"
  echo "Error: Wi-Fi hotspot SSID, password, and mitmproxy web password are required."
  exit 1
fi

HOTSPOT_SSID="$1"
HOTSPOT_PASSWORD="$2"
MITMPROXY_PASSWORD="$3"

bash ./setup-scripts/setup-docker.sh
bash ./setup-scripts/setup-hotspot.sh "$HOTSPOT_SSID" "$HOTSPOT_PASSWORD"
bash ./setup-scripts/setup-mitmproxy.sh "$MITMPROXY_PASSWORD"
bash ./setup-scripts/setup-tui.sh

# Always run this last
bash ./setup-scripts/setup-ssh-welcome.sh
