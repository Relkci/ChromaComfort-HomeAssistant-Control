#!/bin/bash

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer as root (sudo $0)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/chromacomfort"
CONFIG_DIR="/etc/chromacomfort"
AUDIO_USER="chromaudio"
DEFAULT_AIRPLAY_NAME="Bathroom Speaker"

prompt_default() {
    local var_name="$1"
    local prompt="$2"
    local default="$3"
    local value
    read -r -p "$prompt [$default]: " value
    printf -v "$var_name" '%s' "${value:-$default}"
}

echo "ChromaComfort Linux / Home Assistant / AirPlay installer"
echo
echo "Pair and trust the ChromaComfort device with bluetoothctl before running"
echo "the final service tests. The tested device may request legacy PIN 1234."
echo

read -r -p "ChromaComfort Bluetooth MAC (AA:BB:CC:DD:EE:FF): " BT_MAC
if [[ ! "$BT_MAC" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]]; then
    echo "Invalid Bluetooth MAC address." >&2
    exit 1
fi
BT_MAC="${BT_MAC^^}"
BT_MAC_UNDERSCORE="${BT_MAC//:/_}"

prompt_default RFCOMM_CHANNEL "RFCOMM Serial Port channel" "7"
read -r -p "MQTT broker hostname/IP: " MQTT_HOST
prompt_default MQTT_PORT "MQTT port" "1883"
prompt_default MQTT_USER "MQTT username" "chromacomfort"
read -r -s -p "MQTT password: " MQTT_PASSWORD
echo
prompt_default TOPIC_PREFIX "MQTT topic prefix" "chromacomfort/bathroom"
prompt_default DEVICE_NAME "Home Assistant device name" "Bathroom ChromaComfort"
prompt_default DEVICE_ID "Home Assistant device ID" "chromacomfort_bathroom"
prompt_default AIRPLAY_NAME "AirPlay speaker name" "$DEFAULT_AIRPLAY_NAME"

echo
echo "Installing packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    bluetooth bluez bluez-tools \
    python3 python3-venv \
    pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils \
    shairport-sync sudo

systemctl enable --now bluetooth.service

if ! id "$AUDIO_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$AUDIO_USER"
fi
if getent group audio >/dev/null; then
    usermod -aG audio "$AUDIO_USER"
fi
if getent group bluetooth >/dev/null; then
    usermod -aG bluetooth "$AUDIO_USER"
fi
AUDIO_UID="$(id -u "$AUDIO_USER")"
loginctl enable-linger "$AUDIO_USER"
systemctl start "user@${AUDIO_UID}.service"

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/sounds" "$CONFIG_DIR"
cp "$REPO_DIR/chromacomfort_bridge.py" "$INSTALL_DIR/"
cp "$REPO_DIR/chromacomfort_audio_status.py" "$INSTALL_DIR/"
cp "$REPO_DIR/requirements-linux.txt" "$INSTALL_DIR/"
cp "$REPO_DIR/scripts/generate-alert-sounds.py" "$INSTALL_DIR/"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements-linux.txt"
python3 "$INSTALL_DIR/generate-alert-sounds.py" --output-dir "$INSTALL_DIR/sounds"
chmod 644 "$INSTALL_DIR/sounds"/*.wav

cat >"$CONFIG_DIR/chromacomfort.conf" <<EOF
[bluetooth]
address = $BT_MAC
rfcomm_channel = $RFCOMM_CHANNEL
rfcomm_number = 0
rfcomm_device = /dev/rfcomm0
reconnect_seconds = 5
command_retry_seconds = 0.10
command_timeout_seconds = 5

[mqtt]
host = $MQTT_HOST
port = $MQTT_PORT
username = $MQTT_USER
password = $MQTT_PASSWORD
topic_prefix = $TOPIC_PREFIX
discovery_prefix = homeassistant

[device]
name = $DEVICE_NAME
id = $DEVICE_ID

[audio]
airplay_name = $AIRPLAY_NAME
EOF
chmod 600 "$CONFIG_DIR/chromacomfort.conf"

# Control bridge and BlueZ boot recovery.
cp "$REPO_DIR/systemd/chromacomfort.service" /etc/systemd/system/chromacomfort.service
cp "$REPO_DIR/scripts/chromacomfort-bluetooth-ready.sh" /usr/local/sbin/chromacomfort-bluetooth-ready.sh
chmod 755 /usr/local/sbin/chromacomfort-bluetooth-ready.sh
cp "$REPO_DIR/systemd/chromacomfort-bluetooth-ready.service" /etc/systemd/system/chromacomfort-bluetooth-ready.service
mkdir -p /etc/systemd/system/chromacomfort.service.d
cp "$REPO_DIR/systemd/chromacomfort.service.d/bluetooth-ready.conf" \
   /etc/systemd/system/chromacomfort.service.d/bluetooth-ready.conf

# Install one-command diagnostic helper.
cp "$REPO_DIR/scripts/chromacomfort-status.sh" /usr/local/bin/chromacomfort-status
chmod 755 /usr/local/bin/chromacomfort-status

# Headless PipeWire/WirePlumber user configuration.
WP_DIR="/home/$AUDIO_USER/.config/wireplumber/wireplumber.conf.d"
mkdir -p "$WP_DIR"
cp "$REPO_DIR/wireplumber/90-headless-bluetooth.conf" "$WP_DIR/"
cp "$REPO_DIR/wireplumber/91-chromacomfort-no-suspend.conf" "$WP_DIR/"
chown -R "$AUDIO_USER:$AUDIO_USER" "/home/$AUDIO_USER/.config"

sudo -u "$AUDIO_USER" \
    XDG_RUNTIME_DIR="/run/user/$AUDIO_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUDIO_UID/bus" \
    systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service

# Shairport Sync configuration and user service.
SHAIRPORT_DIR="/home/$AUDIO_USER/.config/shairport-sync"
USER_SYSTEMD_DIR="/home/$AUDIO_USER/.config/systemd/user"
mkdir -p "$SHAIRPORT_DIR" "$USER_SYSTEMD_DIR"
sed "s/__AIRPLAY_NAME__/${AIRPLAY_NAME//\//\\\/}/g" \
    "$REPO_DIR/shairport/shairport-sync.conf.example" \
    >"$SHAIRPORT_DIR/shairport-sync.conf"

sed \
    -e "s/__AIRPLAY_NAME__/${AIRPLAY_NAME//\//\\\/}/g" \
    -e "s/__AUDIO_UID__/$AUDIO_UID/g" \
    -e "s/__AUDIO_USER__/$AUDIO_USER/g" \
    "$REPO_DIR/shairport/shairport-sync.service.example" \
    >"$USER_SYSTEMD_DIR/shairport-sync.service"
chown -R "$AUDIO_USER:$AUDIO_USER" "/home/$AUDIO_USER/.config"

systemctl disable --now shairport-sync.service 2>/dev/null || true
sudo -u "$AUDIO_USER" \
    XDG_RUNTIME_DIR="/run/user/$AUDIO_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUDIO_UID/bus" \
    systemctl --user daemon-reload
sudo -u "$AUDIO_USER" \
    XDG_RUNTIME_DIR="/run/user/$AUDIO_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUDIO_UID/bus" \
    systemctl --user enable shairport-sync.service

# A2DP boot recovery. Shairport is restarted only after the sink is confirmed.
sed \
    -e "s/__BLUETOOTH_MAC__/$BT_MAC/g" \
    -e "s/__BLUETOOTH_MAC_UNDERSCORE__/$BT_MAC_UNDERSCORE/g" \
    -e "s/__AUDIO_USER__/$AUDIO_USER/g" \
    "$REPO_DIR/scripts/chromacomfort-audio-ready.sh.example" \
    >/usr/local/sbin/chromacomfort-audio-ready.sh
chmod 755 /usr/local/sbin/chromacomfort-audio-ready.sh

sed "s/__AUDIO_UID__/$AUDIO_UID/g" \
    "$REPO_DIR/systemd/chromacomfort-audio-ready.service.example" \
    >/etc/systemd/system/chromacomfort-audio-ready.service

# MQTT audio-health and alert listener.
cp "$REPO_DIR/systemd/chromacomfort-audio-status.service" \
   /etc/systemd/system/chromacomfort-audio-status.service

systemctl daemon-reload
systemctl enable \
    chromacomfort-bluetooth-ready.service \
    chromacomfort.service \
    chromacomfort-audio-ready.service \
    chromacomfort-audio-status.service

sudo -u "$AUDIO_USER" \
    XDG_RUNTIME_DIR="/run/user/$AUDIO_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUDIO_UID/bus" \
    systemctl --user restart wireplumber.service

if timeout 5 bluetoothctl info "$BT_MAC" 2>/dev/null | grep -q 'Paired: yes'; then
    echo "Bluetooth device is paired. Starting services..."
    systemctl restart chromacomfort-bluetooth-ready.service
    systemctl restart chromacomfort.service
    systemctl restart chromacomfort-audio-ready.service || true
    systemctl restart chromacomfort-audio-status.service
else
    echo
    echo "The device does not appear to be paired yet. Pair/trust it with:"
    echo "  bluetoothctl"
    echo "  power on"
    echo "  agent on"
    echo "  default-agent"
    echo "  scan on"
    echo "  pair $BT_MAC"
    echo "  trust $BT_MAC"
    echo
    echo "Then reboot or start the ChromaComfort services manually."
fi

echo
echo "Installation complete."
echo "Bridge config: $CONFIG_DIR/chromacomfort.conf"
echo "AirPlay name: $AIRPLAY_NAME"
echo "Audio user: $AUDIO_USER (UID $AUDIO_UID)"
echo "Built-in alerts: doorbell, complete, alert, notification"
echo "MQTT play topic: $TOPIC_PREFIX/audio/play"
echo
echo "Useful checks:"
echo "  chromacomfort-status"
echo "  systemctl status chromacomfort --no-pager"
echo "  systemctl status chromacomfort-bluetooth-ready --no-pager"
echo "  systemctl status chromacomfort-audio-ready --no-pager"
echo "  systemctl status chromacomfort-audio-status --no-pager"
echo "  sudo -u $AUDIO_USER XDG_RUNTIME_DIR=/run/user/$AUDIO_UID wpctl status"
