#!/bin/bash
#
# Raspberry Pi Wi-Fi Hotspot Setup Script
# Routes wireless clients' traffic through the ethernet (eth0) connection.
#
# Usage: sudo bash setup-hotspot.sh [SSID] [PASSWORD]
#
# Defaults:
#   SSID:     PiHotspot
#   Password: raspberry123
#   Channel:  36
#   IP range: 192.168.4.x
#
# Requirements: Raspberry Pi with built-in Wi-Fi (Pi 3/4/5) or USB Wi-Fi adapter
#               and an active ethernet connection to your router/modem.

set -e

GREEN='\033[0;32m'
RESET='\033[0m'

# --- Configuration ---
SSID="${1:-PiHotspot}"
PASSWORD="${2:-raspberry123}"
CHANNEL=36
AP_IP="192.168.4.1"
DHCP_RANGE_START="192.168.4.2"
DHCP_RANGE_END="192.168.4.20"
DHCP_LEASE="24h"
WLAN_IFACE="wlan0"
ETH_IFACE="eth0"
COUNTRY_CODE="US"

# --- Validation ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

if [ ${#PASSWORD} -lt 8 ]; then
    echo "ERROR: Wi-Fi password must be at least 8 characters."
    exit 1
fi

echo "============================================"
echo "  Raspberry Pi Wi-Fi Hotspot Setup"
echo "============================================"
echo "  SSID:       $SSID"
echo "  Password:   $PASSWORD"
echo "  AP IP:      $AP_IP"
echo "  DHCP Range: $DHCP_RANGE_START - $DHCP_RANGE_END"
echo "  Wi-Fi:      $WLAN_IFACE"
echo "  Upstream:   $ETH_IFACE"
echo "============================================"
echo ""

# --- Install dependencies ---
echo "[1/6] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq iptables rfkill

# Stop services while configuring
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# --- Configure static IP for wlan0 ---
echo "[2/6] Configuring static IP for $WLAN_IFACE..."

# Prevent dhcpcd from managing wlan0
if [ -f /etc/dhcpcd.conf ]; then
    # Remove any existing wlan0 static config
    sed -i '/^# Pi Hotspot static config/,/^$/d' /etc/dhcpcd.conf

    cat >> /etc/dhcpcd.conf <<EOF

# Pi Hotspot static config
interface $WLAN_IFACE
    static ip_address=${AP_IP}/24
    nohook wpa_supplicant

EOF
fi

# Also configure via NetworkManager if present
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    nmcli device set "$WLAN_IFACE" managed no 2>/dev/null || true
fi

# --- Configure dnsmasq (DHCP server) ---
echo "[3/6] Configuring dnsmasq (DHCP server)..."

# Back up original config
if [ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.orig ]; then
    mv /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
fi

cat > /etc/dnsmasq.conf <<EOF
# Pi Hotspot dnsmasq configuration
interface=$WLAN_IFACE
dhcp-range=$DHCP_RANGE_START,$DHCP_RANGE_END,255.255.255.0,$DHCP_LEASE
domain=local
address=/gw.local/$AP_IP

# Upstream DNS servers
server=8.8.8.8
server=8.8.4.4
EOF

# --- Configure hostapd (Access Point) ---
echo "[4/6] Configuring hostapd (Access Point)..."

cat > /etc/hostapd/hostapd.conf <<EOF
interface=$WLAN_IFACE
driver=nl80211
ssid=$SSID
hw_mode=a
channel=$CHANNEL
wmm_enabled=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
country_code=$COUNTRY_CODE

# 802.11n (HT) support
ieee80211n=1
require_ht=1
ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]

# Security
wpa=2
wpa_passphrase=$PASSWORD
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

# Point hostapd to its config
sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd 2>/dev/null || true

# Also create the override for systemd-based setups
mkdir -p /etc/systemd/system/hostapd.service.d
cat > /etc/systemd/system/hostapd.service.d/override.conf <<EOF
[Service]
Type=simple
ExecStartPre=/usr/sbin/rfkill unblock wifi
ExecStartPre=-/sbin/ip addr flush dev ${WLAN_IFACE}
ExecStartPre=/sbin/ip addr add ${AP_IP}/24 dev ${WLAN_IFACE}
ExecStartPre=/sbin/ip link set ${WLAN_IFACE} up
ExecStart=
ExecStart=/usr/sbin/hostapd /etc/hostapd/hostapd.conf
EOF

# Ensure dnsmasq starts after hostapd on boot (so wlan0 has its IP first)
mkdir -p /etc/systemd/system/dnsmasq.service.d
cat > /etc/systemd/system/dnsmasq.service.d/after-hostapd.conf <<EOF
[Unit]
After=hostapd.service
EOF

# --- Enable IP forwarding ---
echo "[5/6] Enabling IP forwarding and NAT..."

# Persistent IP forwarding — use a drop-in file (works even without /etc/sysctl.conf)
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/90-hotspot.conf
sysctl -w net.ipv4.ip_forward=1

# Set up NAT with iptables
iptables -t nat -C POSTROUTING -o "$ETH_IFACE" -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -o "$ETH_IFACE" -j MASQUERADE

iptables -C FORWARD -i "$ETH_IFACE" -o "$WLAN_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -i "$ETH_IFACE" -o "$WLAN_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT

iptables -C FORWARD -i "$WLAN_IFACE" -o "$ETH_IFACE" -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -i "$WLAN_IFACE" -o "$ETH_IFACE" -j ACCEPT

# Save iptables rules so they persist across reboots
iptables-save > /etc/iptables.ipv4.nat

# Create a systemd service to restore rules on boot (works on Trixie without rc.local)
cat > /etc/systemd/system/iptables-restore.service <<SVCEOF
[Unit]
Description=Restore iptables NAT rules
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c "iptables-restore < /etc/iptables.ipv4.nat"

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl enable iptables-restore.service

# --- Enable and start services ---
echo "[6/6] Enabling and starting services..."

systemctl daemon-reload
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

# Set Wi-Fi regulatory domain for 5 GHz operation
iw reg set "$COUNTRY_CODE"
rfkill unblock wifi

# Bring up the interface with the static IP
ip addr flush dev "$WLAN_IFACE" 2>/dev/null || true
ip addr add "${AP_IP}/24" dev "$WLAN_IFACE" 2>/dev/null || true
ip link set "$WLAN_IFACE" up

systemctl restart dnsmasq
systemctl restart hostapd

echo ""
echo -e "${GREEN}============================================"
echo "  Hotspot setup complete!"
echo "============================================"
echo ""
echo "  SSID:     $SSID"
echo "  Password: $PASSWORD"
echo "  Gateway:  $AP_IP"
echo ""
echo "  Connect your devices to the Wi-Fi network."
echo "  Traffic will be routed through $ETH_IFACE."
echo ""
echo "  If the hotspot doesn't appear, try rebooting:"
echo "    sudo reboot"
echo "  If you still don't see the hotspot, 5 GHZ may"
echo "  not be supported by your Wi-Fi adapter"
echo "  or you may need to try another channel."
echo ""
echo "  To stop the hotspot:"
echo "    sudo systemctl stop hostapd dnsmasq"
echo ""
echo "  To disable it permanently:"
echo "    sudo systemctl disable hostapd dnsmasq"
echo -e "============================================${RESET}"
