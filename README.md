# ChromaComfort Home Assistant + AirPlay Bridge

This project provides Python tools and a tested Linux bridge for controlling and monitoring a Broan/NuTone ChromaComfort-Sensonic fan/light/speaker over Bluetooth Classic.

https://broan-nutone.com/en-us/product/ventilationfans/spk110rgbl

| ![Chroma Comfort](img/ChromaComfort.png) |
|--------------------|

The Linux integration can provide both of these functions from one Bluetooth host:

- Home Assistant control/status over MQTT using Bluetooth RFCOMM/SPP
- AirPlay audio to the ChromaComfort Bluetooth speaker using Shairport Sync, PipeWire/WirePlumber, and BlueZ A2DP

This avoids requiring a dedicated ESP32 for control and also solves the practical problem that the ChromaComfort normally expects a single paired Bluetooth host. The Linux bridge owns the Bluetooth relationship and exposes higher-level network interfaces to the rest of the home.

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

## Home Assistant features

MQTT Discovery is published automatically. The tested bridge exposes:

- Fan on/off
- White light on/off and brightness
- RGB light on/off, color, and brightness
- Wall RGB mode
- Bridge diagnostics including connection state, packet counters, ACK count, last command/error, uptime, and raw brightness
- Audio diagnostics including overall audio readiness, A2DP sink presence, AirPlay service state, AirPlay stream state, and current audio output

No manual Home Assistant entity YAML is required when MQTT discovery is enabled.

## AirPlay features

The optional Linux audio setup exposes the ChromaComfort speaker as an AirPlay destination, for example `Bathroom Speaker`.

The tested Shairport configuration retains Spotify/AirPlay volume control while limiting the software attenuation range so normal listening levels remain audible. The Linux boot services also recover behaviors observed with the tested device:

- BlueZ can occasionally be unresponsive immediately after boot.
- The device can report Bluetooth `Connected: yes` because RFCOMM is active while the A2DP PipeWire sink is still missing.
- Shairport Sync can start before the Bluetooth A2DP sink exists, leaving AirPlay apparently connected but silent. The audio-readiness service now restarts Shairport only after confirming the ChromaComfort A2DP sink.

The supplied readiness services verify the actual required state and retry/recover automatically. `chromacomfort-status` provides a one-command PASS/WARN/FAIL overview plus detailed Bluetooth, PipeWire, Shairport, and service diagnostics.

## Quick Linux installation

See [LINUX.md](LINUX.md) for prerequisites, pairing, manual testing, troubleshooting, and architecture details.

On a Debian/Ubuntu host:

```bash
git clone https://github.com/Relkci/ChromaComfort-Python-Control.git
cd ChromaComfort-Python-Control
sudo bash scripts/install-linux.sh
```

The installer prompts for:

- ChromaComfort Bluetooth MAC address
- RFCOMM channel (tested default: 7)
- MQTT broker and credentials
- Home Assistant device/topic names
- AirPlay speaker name

Pair and trust the ChromaComfort with BlueZ as documented in `LINUX.md`. The tested unit may request legacy PIN `1234`.

## Windows / direct serial utility

`chromacomfort_control_status.py` can still be used directly with a paired serial port, for example:

```powershell
python .\chromacomfort_control_status.py COM4 status
python .\chromacomfort_control_status.py COM4 fan-on
python .\chromacomfort_control_status.py COM4 fan-off
python .\chromacomfort_control_status.py COM4 light-on
python .\chromacomfort_control_status.py COM4 light-off
python .\chromacomfort_control_status.py COM4 rgb-on
python .\chromacomfort_control_status.py COM4 rgb-off
```

After a successful command the script continues listening briefly for an ACK and updated status.

## Linux components

The repository includes:

```text
chromacomfort_bridge.py                 MQTT/RFCOMM bridge
chromacomfort_audio_status.py           MQTT AirPlay/A2DP health publisher
config/chromacomfort.conf.example       bridge configuration example
systemd/chromacomfort.service           control bridge service
systemd/chromacomfort-audio-status.service
scripts/install-linux.sh                guided Linux installer
scripts/chromacomfort-status.sh         one-command diagnostics
scripts/chromacomfort-bluetooth-ready.sh
scripts/chromacomfort-audio-ready.sh.example
wireplumber/                            headless Bluetooth/audio rules
shairport/                              AirPlay configuration/service template
LINUX.md                                full Linux documentation
```

## Tested behavior

The current Linux implementation has been validated with simultaneous RFCOMM control and A2DP audio from the same Linux Bluetooth host. Fan/light/RGB controls work through Home Assistant while the speaker remains usable through AirPlay. Boot testing identified separate BlueZ, A2DP-sink, and Shairport-startup races; recovery logic is included for each. As with any Bluetooth setup, testing on additional hardware and Linux distributions is welcome.

Bluetooth implementations and ChromaComfort hardware revisions may differ, so verify the Serial Port RFCOMM channel with `sdptool browse <MAC>` rather than assuming channel 7 universally.

## Acknowledgements

This project builds on the reverse-engineering work of [Taylor Finnell](https://gist.github.com/taylorfinnell/5349b8085d57836a45be7637055e0692), which provided the foundational ChromaComfort Bluetooth serial protocol behavior. Additional Linux, MQTT, Home Assistant, RGB/brightness, boot recovery, and AirPlay integration behavior was developed and validated experimentally.
