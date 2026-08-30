#!/bin/bash

set -u

LOG_TAG="chromacomfort-bluetooth-ready"

log() {
    logger -t "$LOG_TAG" "$*"
    echo "$*"
}

bluetooth_ready() {
    timeout 5s bluetoothctl show >/dev/null 2>&1
}

log "Checking BlueZ readiness..."

for attempt in 1 2 3 4 5; do
    if bluetooth_ready; then
        log "BlueZ is responsive."
        timeout 5s bluetoothctl power on >/dev/null 2>&1 || true
        exit 0
    fi

    log "BlueZ not responsive yet (attempt $attempt/5)."
    sleep 3
done

log "BlueZ appears unresponsive. Restarting bluetooth.service..."
systemctl restart bluetooth.service
sleep 3

for attempt in 1 2 3 4 5; do
    if bluetooth_ready; then
        log "BlueZ recovered after restart."
        timeout 5s bluetoothctl power on >/dev/null 2>&1 || true
        exit 0
    fi

    log "Waiting for BlueZ after restart (attempt $attempt/5)."
    sleep 3
done

log "ERROR: BlueZ is still unresponsive after restart."
exit 1
