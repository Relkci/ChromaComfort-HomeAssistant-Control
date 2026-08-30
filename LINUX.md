# Linux Bluetooth / MQTT / AirPlay Bridge

> **Status:** tested successfully on Linux with BlueZ RFCOMM/SPP, MQTT, Home Assistant discovery, fan control, white-light on/off and brightness, RGB color/brightness, wall RGB mode, simultaneous Bluetooth A2DP audio, Shairport Sync/AirPlay, Spotify source-volume control, and automatic recovery after reboot.

## Architecture

```text
                         ChromaComfort-Sensonic
                         /                    \
                RFCOMM / SPP                A2DP
                      /                        \
      chromacomfort_bridge.py               BlueZ
                 |                             |
                MQTT                    PipeWire/WirePlumber
                 |                             |
          Home Assistant                Shairport Sync
                                               |
                                            AirPlay
                                               |
                                         Spotify/iPhone
```

The important design point is that one Linux host remains the ChromaComfort's Bluetooth peer while Home Assistant and audio clients communicate with that Linux host over the network.

## Functional Home Assistant entities

The bridge advertises MQTT Discovery entities for:

- Fan
- White Light (on/off and brightness)
- RGB Light (on/off, RGB color, brightness)
- Wall RGB Mode

RGB color uses the Favorite Color 1 save/activate behavior identified in Taylor Finnell's reverse engineering. Current RGB values do not appear in the periodic status packet, so the bridge remembers colors it successfully commands. Brightness is present in device status and is converted between the ChromaComfort 0-100 range and Home Assistant's 0-255 light range.

Diagnostic entities include:

- Bridge Status
- Bluetooth Connected
- MQTT Connected
- Last Error
- Last Command
- Last ACK
- TX Packets
- RX Packets
- ACK Count
- Bridge Uptime
- Brightness Raw

## Tested Linux stack

The integration was developed and validated with:

- BlueZ Bluetooth Classic
- Linux RFCOMM `/dev/rfcomm0`
- Python 3 / pyserial
- Mosquitto-compatible MQTT broker
- Home Assistant MQTT Discovery
- PipeWire
- pipewire-pulse
- WirePlumber 0.5+
- Shairport Sync using its PulseAudio (`pa`) backend through pipewire-pulse

The tested ChromaComfort-Sensonic exposes both Serial Port Profile and Audio Sink services. Simultaneous SPP/RFCOMM control and A2DP playback from the same Linux host has been validated.

## Prerequisites

On Debian/Ubuntu, the installer handles required packages. For manual installation:

```bash
sudo apt update
sudo apt install -y \
  bluetooth bluez bluez-tools \
  python3 python3-venv \
  pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils \
  shairport-sync sudo
sudo systemctl enable --now bluetooth
```

Confirm a Bluetooth controller exists:

```bash
bluetoothctl list
```

## Pair and trust the ChromaComfort

Use BlueZ interactively:

```bash
bluetoothctl
```

Then:

```text
power on
agent on
default-agent
scan on
```

Locate the ChromaComfort address, then:

```text
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

The reverse-engineered implementation indicates legacy PIN `1234` if BlueZ requests one.

Verify:

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF
```

Expected fields include `Paired: yes` and `Trusted: yes`.

## Determine the RFCOMM channel

The tested unit exposes its Serial Port service on RFCOMM channel 7. Verify your hardware:

```bash
sdptool browse AA:BB:CC:DD:EE:FF
```

Locate the `Serial Port` service and its `Channel`. Use that value in the installer/configuration.

A low-level test is:

```bash
sudo rfcomm connect 0 AA:BB:CC:DD:EE:FF 7
```

Leave that command running and, from another terminal, use `chromacomfort_control_status.py /dev/rfcomm0 ...` if you want to test the serial protocol before installing the daemon.

## Recommended installation

Clone the repository and run the guided installer:

```bash
git clone https://github.com/Relkci/ChromaComfort-Python-Control.git
cd ChromaComfort-Python-Control
sudo bash scripts/install-linux.sh
```

The installer asks for:

- Bluetooth MAC address
- RFCOMM channel
- MQTT broker, port, username, and password
- MQTT topic prefix
- Home Assistant device name/ID
- AirPlay speaker name

It installs the bridge under `/opt/chromacomfort`, writes the private configuration to `/etc/chromacomfort/chromacomfort.conf`, creates a dedicated `chromaudio` user, enables user lingering, configures PipeWire/WirePlumber, installs Shairport Sync as a user service, and installs boot recovery services.

MQTT credentials are stored in `/etc/chromacomfort/chromacomfort.conf` with mode `0600`.

## Manual bridge test

Before systemd, you can test the bridge interactively:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements-linux.txt
cp config/chromacomfort.conf.example chromacomfort.conf
nano chromacomfort.conf
./venv/bin/python chromacomfort_bridge.py --config ./chromacomfort.conf --debug
```

The example MQTT address is from a documentation-only network. Replace it with your broker.

## MQTT topics

With the default example prefix:

```text
chromacomfort/bathroom/fan/state
chromacomfort/bathroom/fan/set
chromacomfort/bathroom/white/state
chromacomfort/bathroom/white/set
chromacomfort/bathroom/white/brightness/state
chromacomfort/bathroom/white/brightness/set
chromacomfort/bathroom/rgb/state
chromacomfort/bathroom/rgb/set
chromacomfort/bathroom/rgb/color/state
chromacomfort/bathroom/rgb/color/set
chromacomfort/bathroom/rgb/brightness/state
chromacomfort/bathroom/rgb/brightness/set
chromacomfort/bathroom/wall_rgb/state
chromacomfort/bathroom/wall_rgb/set
chromacomfort/bathroom/bridge/status
chromacomfort/bathroom/bridge/availability
chromacomfort/bathroom/bridge/bluetooth_connected
chromacomfort/bathroom/bridge/mqtt_connected
chromacomfort/bathroom/bridge/last_error
chromacomfort/bathroom/bridge/last_command
chromacomfort/bathroom/bridge/last_ack
chromacomfort/bathroom/bridge/tx_packets
chromacomfort/bathroom/bridge/rx_packets
chromacomfort/bathroom/bridge/ack_count
chromacomfort/bathroom/bridge/uptime
chromacomfort/bathroom/bridge/raw_status
chromacomfort/bathroom/bridge/brightness_raw
```

Discovery publishes under `homeassistant/...` by default.

## Headless Bluetooth audio

A dedicated lingering user (`chromaudio` by default) owns the PipeWire/WirePlumber/Shairport audio session.

WirePlumber normally limits Bluetooth audio management to an active local seat. On a headless bridge, the supplied configuration disables BlueZ seat monitoring:

```ini
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

The repository also includes a rule that disables WirePlumber's normal Bluetooth sink suspend timeout for this dedicated always-on use case.

These files are under `wireplumber/` and are installed into:

```text
/home/chromaudio/.config/wireplumber/wireplumber.conf.d/
```

## Shairport Sync / Spotify volume

The tested Ubuntu package exposes Shairport's PulseAudio backend rather than a native PipeWire backend. `pipewire-pulse` provides the compatibility layer.

The working configuration uses:

```conf
general =
{
    name = "Bathroom Speaker";
    output_backend = "pa";
    disable_synchronization = "yes";
    ignore_volume_control = "no";
    volume_range_db = 30;
    volume_max_db = 0.0;
    volume_control_profile = "flat";
};

pa =
{
    application_name = "Bathroom Speaker";
};
```

Two settings were important in testing:

- `disable_synchronization = "yes"` avoids Shairport's normal resynchronization behavior fighting Bluetooth A2DP latency.
- `volume_range_db = 30` preserves AirPlay/Spotify volume control while avoiding the very large software attenuation range that made playback effectively inaudible at normal source-volume settings.

The distribution's system-level Shairport service is disabled. Shairport runs as the dedicated audio user's systemd user service so it connects to the correct PipeWire/PulseAudio runtime.

## Why the boot recovery services exist

Two device/stack behaviors were reproduced during testing.

### BlueZ can be unresponsive after boot

When this occurs, even `bluetoothctl show` can hang. The RFCOMM bridge cannot recover while BlueZ itself is wedged.

`chromacomfort-bluetooth-ready.service` runs a bounded `bluetoothctl show` health check. It waits for normal initialization and only restarts `bluetooth.service` if BlueZ remains unresponsive.

### Bluetooth Connected does not guarantee A2DP

The ChromaComfort can report:

```text
Connected: yes
```

because the RFCOMM/SPP control session is active, while PipeWire still has no `bluez_output...` A2DP sink.

`chromacomfort-audio-ready.service` therefore checks the actual PipeWire sink instead of trusting the generic Bluetooth `Connected` flag. If the sink is missing, it disconnects/reconnects the device and retries until WirePlumber creates the A2DP sink. The tested device occasionally returns a page timeout on the first connection attempt, so retries are intentional.

## Service checks

Control bridge:

```bash
systemctl status chromacomfort --no-pager
journalctl -u chromacomfort -f
```

BlueZ readiness:

```bash
systemctl status chromacomfort-bluetooth-ready --no-pager
journalctl -u chromacomfort-bluetooth-ready -b --no-pager
```

A2DP readiness:

```bash
systemctl status chromacomfort-audio-ready --no-pager
journalctl -u chromacomfort-audio-ready -b --no-pager
```

PipeWire/WirePlumber:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID wpctl status
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pactl list short sinks
```

A healthy audio state should include a sink named similarly to:

```text
bluez_output.AA_BB_CC_DD_EE_FF.1
```

and Shairport's `Bathroom Speaker` stream should route to the ChromaComfort sink rather than a built-in ALSA device.

Shairport user service:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio \
  XDG_RUNTIME_DIR=/run/user/$CHROMA_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$CHROMA_UID/bus \
  systemctl --user status shairport-sync.service --no-pager
```

## Reboot validation

After installation, reboot the Linux host and verify all of the following without manual intervention:

1. Home Assistant rediscovers/retains the MQTT device.
2. RX/TX packet counters begin changing.
3. Fan and light commands physically work.
4. `wpctl status` shows the ChromaComfort `[bluez5]` device and sink.
5. The configured AirPlay speaker appears.
6. Spotify/AirPlay playback is audible.
7. Source volume control changes speaker output level.

The supplied configuration has passed this sequence on the development/test host.

## Protocol/testing notes

1. The daemon assumes the ChromaComfort is paired and trusted by BlueZ.
2. The tested unit uses Bluetooth Classic SPP/RFCOMM channel 7.
3. Fan, white light, wall RGB, white brightness, RGB Favorite Color, and RGB brightness have been validated from Linux through Home Assistant/MQTT.
4. Incoming status includes current brightness but does not appear to expose selected RGB channel values, so the bridge remembers successfully commanded RGB values.
5. The final byte in observed status/ACK packets is not treated as a checksum because its algorithm/meaning has not been confirmed.
6. Bluetooth numeric PipeWire/WirePlumber object IDs are ephemeral. Scripts use the stable `bluez_output.<MAC>.1` name instead.
7. RFCOMM control and A2DP audio are kept as separate services intentionally. A failure in audio should not require redesigning the MQTT/control daemon.

## Acknowledgement

The protocol work builds on Taylor Finnell's ChromaComfort reverse engineering:

https://gist.github.com/taylorfinnell/5349b8085d57836a45be7637055e0692
