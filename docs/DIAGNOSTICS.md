# ChromaComfort diagnostics

The Linux installer provides a one-command health snapshot:

```bash
chromacomfort-status
```

It reports the state of:

- BlueZ
- ChromaComfort BlueZ readiness service
- ChromaComfort A2DP readiness service
- ChromaComfort MQTT/RFCOMM bridge
- PipeWire
- pipewire-pulse
- WirePlumber
- Shairport Sync
- Bluetooth pairing/connection state
- RFCOMM bindings/processes
- PipeWire/WirePlumber topology
- PulseAudio-compatible sinks
- expected ChromaComfort A2DP sink presence
- Shairport processes
- recent relevant journals

It also prints a short interpretation of whether the Bluetooth A2DP sink exists and whether an audio stream appears routed to it.

## Existing installations

If your installation predates the helper:

```bash
cd /opt/ChromaComfort-Python-Control
git pull
sudo cp scripts/chromacomfort-status.sh /usr/local/bin/chromacomfort-status
sudo chmod 755 /usr/local/bin/chromacomfort-status
chromacomfort-status
```

The script reads the Bluetooth MAC from `/etc/chromacomfort/chromacomfort.conf` by default. Overrides are supported:

```bash
sudo BT_MAC=AA:BB:CC:DD:EE:FF AUDIO_USER=chromaudio chromacomfort-status
```

The stable A2DP sink name is derived from the MAC, for example:

```text
bluez_output.AA_BB_CC_DD_EE_FF.1
```

Do not rely on numeric `wpctl` node IDs because they change between sessions and reboots.
