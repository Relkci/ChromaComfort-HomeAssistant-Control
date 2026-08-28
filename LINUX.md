# Linux Bluetooth / MQTT Bridge

> **Status:** early implementation. The Windows RFCOMM utility has been proven against a real ChromaComfort device. The Linux daemon is prepared for hardware testing and may require adjustment to the RFCOMM channel, BlueZ pairing behavior, and RGB/brightness behavior as testing proceeds.

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

The initial implementation advertises:

- Fan
- White Light
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

Example bridge status messages include:

```text
MQTT connected; initializing Bluetooth
Connecting RFCOMM to 04:57:91:F1:14:AB channel 1
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

Locate the ChromaComfort device, then pair and trust it. Example using the MAC discovered during development:

```text
pair 04:57:91:F1:14:AB
trust 04:57:91:F1:14:AB
```

The reverse-engineered implementation indicates legacy PIN `1234` if BlueZ requests one.

## Determine the RFCOMM channel

Do not assume the example configuration's channel 1 is correct. Inspect the device's SDP services and locate the Serial Port/SPP service. Depending on the distribution/tools installed, `sdptool browse <MAC>` can be used for this.

Update `rfcomm_channel` in the configuration with the discovered channel.

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

## MQTT topics

With the default example prefix, state/command topics include:

```text
chromacomfort/bathroom/fan/state
chromacomfort/bathroom/fan/set
chromacomfort/bathroom/white/state
chromacomfort/bathroom/white/set
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

## Important testing notes

1. The fan's SPP/RFCOMM channel still needs to be confirmed on Linux.
2. The daemon assumes the device is already paired/trusted by BlueZ.
3. Fan, white-light, and wall-RGB command values are based on commands already validated through the Windows utility/reference implementation.
4. RGB Favorite Color and brightness behavior is based on Taylor Finnell's implementation and needs validation on this specific unit from Linux.
5. The final byte in observed status/ACK packets is not currently treated as a checksum because its algorithm/meaning has not been confirmed.
6. Audio/A2DP and Shairport Sync are deliberately separate from this daemon. They will be added after reliable simultaneous SPP control has been established.
