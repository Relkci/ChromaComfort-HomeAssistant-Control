# Linux Bluetooth / MQTT Bridge

> **Status:** tested successfully on Linux with BlueZ RFCOMM/SPP, MQTT, Home Assistant discovery, fan control, white-light on/off and brightness, RGB color/brightness, and wall RGB mode.

## Architecture

```text
Home Assistant / Mosquitto
          |
         MQTT
          |
chromacomfort_bridge.py
          |
 Bluetooth Classic RFCOMM/SPP
          |
 ChromaComfort-Sensonic
```

The bridge publishes Home Assistant MQTT Discovery messages automatically. No manual HA entity YAML should be required once MQTT discovery is enabled.

## Functional entities

The bridge advertises:

- Fan
- White Light (on/off and brightness)
- RGB Light (on/off, RGB color, brightness)
- Wall RGB Mode

RGB color is implemented using the Favorite Color 1 save/activate behavior identified in Taylor Finnell's reverse engineering. Current RGB values do not appear to be present in the periodic status packet, so the bridge remembers colors it successfully commands. Brightness is present in device status and is converted between the ChromaComfort 0-100 range and Home Assistant's 0-255 light range.

## Diagnostic entities

The bridge also advertises diagnostic entities intended to make unattended operation easier to troubleshoot from Home Assistant:

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

Example bridge status messages use fictional addresses:

```text
MQTT connected; initializing Bluetooth
Connecting RFCOMM to AA:BB:CC:DD:EE:FF channel 7
Bluetooth RFCOMM connected; waiting for device status
Connected / Ready
Sending command: fan-on
Command acknowledged: fan-on
Bluetooth disconnected; retrying in 5s
```

Pairing/PIN messages are intentionally not fabricated by the daemon. Pairing is currently performed through BlueZ before the service starts, so the daemon reports only operations it actually performs.

## Linux prerequisites

Install BlueZ, Python, venv support, and useful Bluetooth utilities. Package names vary slightly by distribution. On Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y bluetooth bluez bluez-tools python3 python3-venv
sudo systemctl enable --now bluetooth
```

Confirm a controller exists:

```bash
lsusb
bluetoothctl list
```

## Pair the ChromaComfort

Start BlueZ's interactive utility:

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

Locate the ChromaComfort device, then pair and trust it. Replace the fictional MAC below with the address reported by `bluetoothctl`:

```text
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

The reverse-engineered implementation indicates legacy PIN `1234` if BlueZ requests one.

## Determine the RFCOMM channel

The tested ChromaComfort-Sensonic unit exposes its Serial Port Profile on RFCOMM channel 7. Verify your own device rather than assuming all revisions use the same channel:

```bash
sdptool browse AA:BB:CC:DD:EE:FF
```

Locate the `Serial Port` service and its RFCOMM channel, then update `rfcomm_channel` in the configuration if necessary.

A direct manual transport test can be performed with:

```bash
rfcomm connect 0 AA:BB:CC:DD:EE:FF 7
```

## Manual test installation

```bash
git clone https://github.com/Relkci/ChromaComfort-Python-Control.git
cd ChromaComfort-Python-Control

python3 -m venv venv
./venv/bin/pip install -r requirements-linux.txt

cp config/chromacomfort.conf.example chromacomfort.conf
nano chromacomfort.conf

./venv/bin/python chromacomfort_bridge.py --config ./chromacomfort.conf --debug
```

For the first Linux test, run it interactively with `--debug`. This makes RFCOMM/MQTT failures visible before installing the systemd service.

The example MQTT host is `192.0.2.10`, which is from the documentation-only TEST-NET-1 range. Replace it with the address or hostname of your actual MQTT broker.

## MQTT topics

With the default example prefix, state/command topics include:

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
chromacomfort/bathroom/bridge/last_error
chromacomfort/bathroom/bridge/bluetooth_connected
chromacomfort/bathroom/bridge/brightness_raw
```

The bridge publishes Home Assistant discovery configs under `homeassistant/...` by default.

## systemd installation

After interactive testing succeeds, a suggested installation is:

```bash
sudo useradd --system --home /opt/chromacomfort --shell /usr/sbin/nologin chromacomfort
sudo mkdir -p /opt/chromacomfort /etc/chromacomfort
sudo cp chromacomfort_bridge.py requirements-linux.txt /opt/chromacomfort/
sudo cp config/chromacomfort.conf.example /etc/chromacomfort/chromacomfort.conf
sudo python3 -m venv /opt/chromacomfort/venv
sudo /opt/chromacomfort/venv/bin/pip install -r /opt/chromacomfort/requirements-linux.txt
sudo chown -R chromacomfort:chromacomfort /opt/chromacomfort /etc/chromacomfort
sudo chmod 600 /etc/chromacomfort/chromacomfort.conf
sudo cp systemd/chromacomfort.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chromacomfort
```

Monitor it with:

```bash
systemctl status chromacomfort
journalctl -u chromacomfort -f
```

## Testing notes

1. The daemon assumes the ChromaComfort device is already paired and trusted by BlueZ.
2. The tested unit uses Bluetooth Classic SPP/RFCOMM channel 7.
3. Fan, white-light, wall-RGB, white-light brightness, RGB Favorite Color, and RGB brightness control have been validated from Linux through Home Assistant/MQTT.
4. Incoming status reports the current brightness but does not appear to expose the currently selected RGB channel values, so the bridge remembers successfully commanded RGB values.
5. The final byte in observed status/ACK packets is not currently treated as a checksum because its algorithm/meaning has not been confirmed.
6. Audio/A2DP and Shairport Sync are deliberately separate from this daemon and can be developed independently of the RFCOMM control bridge.
