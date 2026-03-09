#!/usr/bin/env bash
#
# Installs (or updates) the latest rita-devtools-tui release and configures
# RITA_MITM_URL. Safe to re-run — skips the download if already up to date.
#
# Usage: sudo bash setup-tui.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RESET='\033[0m'

if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)." >&2
    exit 1
fi

REPO="mitchgrogg/rita-devtools-tui"
INSTALL_DIR="/usr/local/bin"

# Detect OS and architecture
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  armv7l)  ARCH="arm64" ;;
  *)
    echo "Error: Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

# Get the latest release tag
echo "Fetching latest rita-devtools-tui release..."
TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$TAG" ]; then
  echo "Error: Could not determine latest release." >&2
  exit 1
fi
VERSION="${TAG#v}"

# Skip download if already installed and up to date
INSTALLED_VERSION=""
if command -v rita-devtools-tui &>/dev/null; then
  INSTALLED_VERSION=$(rita-devtools-tui --version 2>/dev/null | awk '{print $2}')
fi

if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
  echo "rita-devtools-tui $TAG is already installed."
else
  ASSET="rita-devtools-tui_${VERSION}_${OS}_${ARCH}.tar.gz"
  URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"

  echo "Downloading $ASSET..."
  TMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TMP_DIR"' EXIT

  curl -fsSL "$URL" -o "$TMP_DIR/$ASSET"
  tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR"
  install -m 0755 "$TMP_DIR/rita-devtools-tui" "$INSTALL_DIR/rita-devtools-tui"
fi

# Set RITA_MITM_URL system-wide so the TUI connects to the local rita-mitm instance
cat > /etc/profile.d/rita-mitm-url.sh <<'EOF'
export RITA_MITM_URL="http://localhost:8082"
EOF

echo ""
echo -e "${GREEN}rita-devtools-tui $TAG installed to $INSTALL_DIR/rita-devtools-tui"
echo -e "RITA_MITM_URL set to http://localhost:8082 (via /etc/profile.d/rita-mitm-url.sh)${RESET}"
echo ""
echo -e "${YELLOW}Log out and log back in to complete setup.${RESET}"
