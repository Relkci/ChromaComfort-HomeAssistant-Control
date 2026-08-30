# ChromaComfort Home Assistant + AirPlay Bridge

This project provides Python tools and a tested Linux bridge for controlling and monitoring a Broan/NuTone ChromaComfort-Sensonic fan/light/speaker over Bluetooth Classic.

https://broan-nutone.com/en-us/product/ventilationfans/spk110rgbl

| ![Chroma Comfort](img/ChromaComfort.png) |
|--------------------|

The Linux integration can provide these functions from one Bluetooth host:

- Home Assistant control/status over MQTT using Bluetooth RFCOMM/SPP
- AirPlay audio to the ChromaComfort Bluetooth speaker using Shairport Sync, PipeWire/WirePlumber, and BlueZ A2DP
- Home Assistant-triggered local alert sounds, mixed into the same PipeWire/A2DP output without interrupting AirPlay

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
                              |                       /             \
                       Home Assistant        Shairport Sync      Alert WAVs
                                                  |                  |
                                               AirPlay          MQTT trigger
                                                  |                  |
                                            Spotify/iPhone     Home Assistant
```

## Home Assistant features

MQTT Discovery is published automatically. The tested bridge exposes:

- Fan on/off
- White light on/off and brightness
- RGB light on/off, color, and brightness
- Wall RGB mode
- Bridge diagnostics including connection state, packet counters, ACK count, last command/error, uptime, and raw brightness
- Audio diagnostics including overall audio readiness, A2DP sink presence, AirPlay service state, AirPlay stream state, current audio output, and last alert
- MQTT-triggered notification sounds such as doorbell and completion chimes

No manual Home Assistant entity YAML is required when MQTT discovery is enabled.

## Home Assistant alert sounds

Home Assistant can trigger short local WAV sounds on the ChromaComfort speaker by publishing a sound name to:

```text
<topic_prefix>/audio/play
```

With the default topic prefix, a Home Assistant action looks like:

```yaml
action:
  - action: mqtt.publish
    data:
      topic: chromacomfort/bathroom/audio/play
      payload: doorbell
```

The built-in sounds are:

- `doorbell`
- `complete`
- `alert`
- `notification`

The WAV files are generated locally during installation/update and stored under `/opt/chromacomfort/sounds/`. No third-party sound files or external downloads are required.

Alerts are sent directly to the ChromaComfort PipeWire A2DP sink using `pw-play`. They do not pause, stop, or restart Shairport Sync. If AirPlay is already playing, PipeWire mixes the alert into the existing audio stream. Alert requests are serialized so several rapid MQTT messages do not create overlapping sounds.

See [docs/ALERTS.md](docs/ALERTS.md) for details and Home Assistant examples.

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

For an existing installation, update in place with:

```bash
cd /opt/ChromaComfort-Python-Control
git pull
sudo bash scripts/update-linux.sh
```

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
chromacomfort_audio_status.py           MQTT AirPlay/A2DP health + alert listener
config/chromacomfort.conf.example       bridge configuration example
systemd/chromacomfort.service           control bridge service
systemd/chromacomfort-audio-status.service
scripts/install-linux.sh                guided Linux installer
scripts/update-linux.sh                 existing-installation updater
scripts/generate-alert-sounds.py        generates built-in WAV notification sounds
scripts/chromacomfort-status.sh         one-command diagnostics
scripts/chromacomfort-bluetooth-ready.sh
scripts/chromacomfort-audio-ready.sh.example
wireplumber/                            headless Bluetooth/audio rules
shairport/                              AirPlay configuration/service template
docs/ALERTS.md                          Home Assistant alert-sound usage
LINUX.md                                full Linux documentation
```

## Tested behavior

The current Linux implementation has been validated with simultaneous RFCOMM control and A2DP audio from the same Linux Bluetooth host. Fan/light/RGB controls work through Home Assistant while the speaker remains usable through AirPlay. Home Assistant-triggered local WAV alerts have also been validated while AirPlay remains available, with PipeWire mixing the alert into the same A2DP output instead of stopping or restarting Shairport. Boot testing identified separate BlueZ, A2DP-sink, and Shairport-startup races; recovery logic is included for each. As with any Bluetooth setup, testing on additional hardware and Linux distributions is welcome.

Bluetooth implementations and ChromaComfort hardware revisions may differ, so verify the Serial Port RFCOMM channel with `sdptool browse <MAC>` rather than assuming channel 7 universally.

## Acknowledgements

This project builds on the reverse-engineering work of [Taylor Finnell](https://gist.github.com/taylorfinnell/5349b8085d57836a45be7637055e0692), which provided the foundational ChromaComfort Bluetooth serial protocol behavior. Additional Linux, MQTT, Home Assistant, RGB/brightness, boot recovery, AirPlay integration, and local alert playback behavior was developed and validated experimentally.
