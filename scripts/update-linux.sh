#!/bin/bash

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this updater as root (sudo $0)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/chromacomfort"
CONFIG="/etc/chromacomfort/chromacomfort.conf"
AUDIO_USER="chromaudio"

if [[ ! -r "$CONFIG" ]]; then
    echo "Existing configuration not found at $CONFIG." >&2
    echo "Run scripts/install-linux.sh for a new installation." >&2
    exit 1
fi

if ! id "$AUDIO_USER" >/dev/null 2>&1; then
    echo "Audio user '$AUDIO_USER' does not exist. Run the full installer." >&2
    exit 1
fi

BT_MAC="$(awk -F'=' '/^[[:space:]]*address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print toupper($2); exit}' "$CONFIG")"
if [[ ! "$BT_MAC" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]]; then
    echo "Could not read a valid Bluetooth MAC from $CONFIG." >&2
    exit 1
fi
BT_MAC_UNDERSCORE="${BT_MAC//:/_}"
AUDIO_UID="$(id -u "$AUDIO_USER")"

# Preserve the configured AirPlay name. Older installations did not have an
# [audio] section, so recover the name from the existing Shairport config.
AIRPLAY_NAME="$(awk -F'=' '
    /^\[audio\]/{inaudio=1; next}
    /^\[/{inaudio=0}
    inaudio && /^[[:space:]]*airplay_name[[:space:]]*=/{
        sub(/^[^=]*=[[:space:]]*/, ""); gsub(/[[:space:]]+$/, ""); print; exit
    }
' "$CONFIG")"

if [[ -z "$AIRPLAY_NAME" && -r "/home/$AUDIO_USER/.config/shairport-sync/shairport-sync.conf" ]]; then
    AIRPLAY_NAME="$(sed -n 's/^[[:space:]]*name[[:space:]]*=[[:space:]]*"\([^"]*\)";.*/\1/p' "/home/$AUDIO_USER/.config/shairport-sync/shairport-sync.conf" | head -n1)"
fi
AIRPLAY_NAME="${AIRPLAY_NAME:-Bathroom Speaker}"

if ! grep -q '^\[audio\]' "$CONFIG"; then
    cat >>"$CONFIG" <<EOF

[audio]
airplay_name = $AIRPLAY_NAME
EOF
    chmod 600 "$CONFIG"
fi

echo "Updating ChromaComfort installation..."
echo "Bluetooth MAC: $BT_MAC"
echo "Audio user: $AUDIO_USER (UID $AUDIO_UID)"
echo "AirPlay name: $AIRPLAY_NAME"

mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/chromacomfort_bridge.py" "$INSTALL_DIR/"
cp "$REPO_DIR/chromacomfort_audio_status.py" "$INSTALL_DIR/"
cp "$REPO_DIR/requirements-linux.txt" "$INSTALL_DIR/"

if [[ ! -x "$INSTALL_DIR/venv/bin/pip" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements-linux.txt"

cp "$REPO_DIR/scripts/chromacomfort-status.sh" /usr/local/bin/chromacomfort-status
chmod 755 /usr/local/bin/chromacomfort-status

cp "$REPO_DIR/scripts/chromacomfort-bluetooth-ready.sh" /usr/local/sbin/chromacomfort-bluetooth-ready.sh
chmod 755 /usr/local/sbin/chromacomfort-bluetooth-ready.sh

sed \
    -e "s/__BLUETOOTH_MAC__/$BT_MAC/g" \
    -e "s/__BLUETOOTH_MAC_UNDERSCORE__/$BT_MAC_UNDERSCORE/g" \
    -e "s/__AUDIO_USER__/$AUDIO_USER/g" \
    "$REPO_DIR/scripts/chromacomfort-audio-ready.sh.example" \
    >/usr/local/sbin/chromacomfort-audio-ready.sh
chmod 755 /usr/local/sbin/chromacomfort-audio-ready.sh

cp "$REPO_DIR/systemd/chromacomfort.service" /etc/systemd/system/chromacomfort.service
cp "$REPO_DIR/systemd/chromacomfort-bluetooth-ready.service" /etc/systemd/system/chromacomfort-bluetooth-ready.service
mkdir -p /etc/systemd/system/chromacomfort.service.d
cp "$REPO_DIR/systemd/chromacomfort.service.d/bluetooth-ready.conf" \
   /etc/systemd/system/chromacomfort.service.d/bluetooth-ready.conf
sed "s/__AUDIO_UID__/$AUDIO_UID/g" \
    "$REPO_DIR/systemd/chromacomfort-audio-ready.service.example" \
    >/etc/systemd/system/chromacomfort-audio-ready.service
cp "$REPO_DIR/systemd/chromacomfort-audio-status.service" \
   /etc/systemd/system/chromacomfort-audio-status.service

# Keep WirePlumber rules current without disturbing the rest of the user's
# audio configuration.
WP_DIR="/home/$AUDIO_USER/.config/wireplumber/wireplumber.conf.d"
mkdir -p "$WP_DIR"
cp "$REPO_DIR/wireplumber/90-headless-bluetooth.conf" "$WP_DIR/"
cp "$REPO_DIR/wireplumber/91-chromacomfort-no-suspend.conf" "$WP_DIR/"
chown -R "$AUDIO_USER:$AUDIO_USER" "/home/$AUDIO_USER/.config"

systemctl daemon-reload
systemctl enable \
    chromacomfort-bluetooth-ready.service \
    chromacomfort.service \
    chromacomfort-audio-ready.service \
    chromacomfort-audio-status.service

# Re-run audio readiness first. This confirms the A2DP sink and then restarts
# Shairport Sync, fixing the silent-AirPlay boot race seen in testing.
systemctl restart chromacomfort-audio-ready.service

# Restart the control bridge so future bridge changes are picked up, then start
# the audio MQTT diagnostics after the audio path is known-good.
systemctl restart chromacomfort.service
systemctl restart chromacomfort-audio-status.service

echo
echo "Update complete."
echo "Run: sudo chromacomfort-status"
