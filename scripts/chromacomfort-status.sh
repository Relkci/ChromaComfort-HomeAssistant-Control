#!/bin/bash

set -u

AUDIO_USER="${AUDIO_USER:-chromaudio}"
BT_MAC="${BT_MAC:-}"
CONFIG="${CONFIG:-/etc/chromacomfort/chromacomfort.conf}"

if [[ -z "$BT_MAC" && -r "$CONFIG" ]]; then
    BT_MAC="$(awk -F'=' '/^[[:space:]]*address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' "$CONFIG")"
fi

if [[ -z "$BT_MAC" ]]; then
    BT_MAC="AA:BB:CC:DD:EE:FF"
fi

AUDIO_UID="$(id -u "$AUDIO_USER" 2>/dev/null || true)"
RUNTIME="${AUDIO_UID:+/run/user/$AUDIO_UID}"
SINK="bluez_output.${BT_MAC//:/_}.1"

heading() {
    printf '\n==== %s ====\n' "$1"
}

system_active() {
    systemctl is-active --quiet "$1" 2>/dev/null
}

user_active() {
    local service="$1"
    [[ -n "$AUDIO_UID" ]] || return 1
    sudo -u "$AUDIO_USER" \
        XDG_RUNTIME_DIR="$RUNTIME" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME/bus" \
        systemctl --user is-active --quiet "$service" 2>/dev/null
}

sink_exists() {
    [[ -n "$AUDIO_UID" ]] || return 1
    sudo -u "$AUDIO_USER" \
        XDG_RUNTIME_DIR="$RUNTIME" \
        PULSE_SERVER="unix:$RUNTIME/pulse/native" \
        pactl list short sinks 2>/dev/null | grep -q "$SINK"
}

stream_routed() {
    [[ -n "$AUDIO_UID" ]] || return 1
    sudo -u "$AUDIO_USER" XDG_RUNTIME_DIR="$RUNTIME" wpctl status 2>/dev/null | \
        grep -A12 'Streams:' | grep -q 'ChromaComfort-Sensonic Speaker'
}

service_state() {
    local service="$1"
    printf '%-40s %s\n' "$service" "$(systemctl is-active "$service" 2>/dev/null || echo unknown)"
}

user_service_state() {
    local service="$1"
    if [[ -z "$AUDIO_UID" ]]; then
        printf '%-40s %s\n' "$service" "audio user missing"
        return
    fi
    printf '%-40s %s\n' "$service" "$(sudo -u "$AUDIO_USER" XDG_RUNTIME_DIR="$RUNTIME" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME/bus" systemctl --user is-active "$service" 2>/dev/null || echo unknown)"
}

heading "Health summary"
FAILS=0
WARNS=0

check_pass_fail() {
    local label="$1"
    shift
    if "$@"; then
        printf '[PASS] %s\n' "$label"
    else
        printf '[FAIL] %s\n' "$label"
        FAILS=$((FAILS + 1))
    fi
}

check_pass_fail "BlueZ service is active" system_active bluetooth.service
check_pass_fail "ChromaComfort control bridge is active" system_active chromacomfort.service
check_pass_fail "Bluetooth boot readiness service completed" system_active chromacomfort-bluetooth-ready.service
check_pass_fail "A2DP boot readiness service completed" system_active chromacomfort-audio-ready.service
check_pass_fail "MQTT audio status publisher is active" system_active chromacomfort-audio-status.service
check_pass_fail "PipeWire is active" user_active pipewire.service
check_pass_fail "pipewire-pulse is active" user_active pipewire-pulse.service
check_pass_fail "WirePlumber is active" user_active wireplumber.service
check_pass_fail "Shairport Sync is active" user_active shairport-sync.service
check_pass_fail "ChromaComfort A2DP sink is present" sink_exists

if stream_routed; then
    printf '[PASS] AirPlay stream is routed to ChromaComfort when a stream exists\n'
else
    printf '[WARN] No current AirPlay stream is visibly routed to ChromaComfort; this is normal when idle\n'
    WARNS=$((WARNS + 1))
fi

printf '\nSummary: %d failure(s), %d warning(s)\n' "$FAILS" "$WARNS"

heading "ChromaComfort summary"
printf '%-24s %s\n' "Bluetooth MAC:" "$BT_MAC"
printf '%-24s %s\n' "Audio user:" "$AUDIO_USER${AUDIO_UID:+ (UID $AUDIO_UID)}"
printf '%-24s %s\n' "Expected A2DP sink:" "$SINK"
printf '%-24s %s\n' "RFCOMM device:" "$(ls /dev/rfcomm* 2>/dev/null | tr '\n' ' ' || true)"
printf '\n'
service_state bluetooth.service
service_state chromacomfort-bluetooth-ready.service
service_state chromacomfort-audio-ready.service
service_state chromacomfort.service
service_state chromacomfort-audio-status.service
user_service_state pipewire.service
user_service_state pipewire-pulse.service
user_service_state wireplumber.service
user_service_state shairport-sync.service

heading "Bluetooth device"
timeout 5 bluetoothctl info "$BT_MAC" 2>&1 | grep -E 'Name:|Alias:|Paired:|Bonded:|Trusted:|Connected:|UUID:' || echo "bluetoothctl info failed or timed out"

heading "RFCOMM"
rfcomm 2>&1 || true
ps -ef | grep '[r]fcomm connect' || true

heading "PipeWire / WirePlumber"
if [[ -n "$AUDIO_UID" ]]; then
    sudo -u "$AUDIO_USER" XDG_RUNTIME_DIR="$RUNTIME" wpctl status 2>&1 || true
else
    echo "Audio user '$AUDIO_USER' not found."
fi

heading "PulseAudio compatibility sinks"
if [[ -n "$AUDIO_UID" ]]; then
    sudo -u "$AUDIO_USER" XDG_RUNTIME_DIR="$RUNTIME" PULSE_SERVER="unix:$RUNTIME/pulse/native" pactl list short sinks 2>&1 || true
    echo
    if sink_exists; then
        echo "A2DP sink: PRESENT"
    else
        echo "A2DP sink: MISSING"
    fi
fi

heading "Shairport processes"
ps -ef | grep '[s]hairport-sync' || echo "No shairport-sync process found"

heading "Recent service logs"
echo "-- chromacomfort-audio-ready --"
journalctl -u chromacomfort-audio-ready.service -b -n 25 --no-pager 2>/dev/null || true

echo
echo "-- chromacomfort-audio-status --"
journalctl -u chromacomfort-audio-status.service -b -n 25 --no-pager 2>/dev/null || true

echo
echo "-- chromacomfort --"
journalctl -u chromacomfort.service -b -n 25 --no-pager 2>/dev/null || true

echo
echo "-- shairport-sync (user) --"
if [[ -n "$AUDIO_UID" ]]; then
    sudo -u "$AUDIO_USER" XDG_RUNTIME_DIR="$RUNTIME" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME/bus" journalctl --user -u shairport-sync.service -b -n 25 --no-pager 2>/dev/null || true
fi

heading "Quick interpretation"
if sink_exists; then
    echo "Bluetooth A2DP sink exists."
else
    echo "Bluetooth A2DP sink is missing. Audio cannot reach the ChromaComfort speaker."
fi

if stream_routed; then
    echo "An audio stream appears routed to the ChromaComfort sink."
else
    echo "No currently visible stream is routed to the ChromaComfort sink. This is expected when AirPlay is idle."
fi
