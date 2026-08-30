# Linux Bluetooth / MQTT / AirPlay Bridge

> **Status:** tested successfully on Linux with BlueZ RFCOMM/SPP, MQTT, Home Assistant discovery, fan control, white-light on/off and brightness, RGB color/brightness, wall RGB mode, simultaneous Bluetooth A2DP audio, Shairport Sync/AirPlay, Spotify source-volume control, Home Assistant-triggered local WAV alerts, automatic recovery after reboot, AirPlay pause/resume recovery, and stable ALSA/PipeWire playback using a 0.5-second Shairport backend buffer.

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
                                             ALSA/PipeWire       MQTT trigger
                                                  |                  |
                                               AirPlay          Home Assistant
                                                  |
                                            Spotify/iPhone
```

The important design point is that one Linux host remains the ChromaComfort's Bluetooth peer while Home Assistant and audio clients communicate with that Linux host over the network.

## Tested Linux stack

The integration was developed and validated with:

- BlueZ Bluetooth Classic
- Linux RFCOMM `/dev/rfcomm0`
- Python 3 / pyserial
- Mosquitto-compatible MQTT broker
- Home Assistant MQTT Discovery
- PipeWire
- pipewire-alsa
- pipewire-pulse
- WirePlumber 0.5+
- Shairport Sync 4.3.7 using its ALSA backend through PipeWire's ALSA plugin

The tested ChromaComfort-Sensonic exposes both Serial Port Profile and Audio Sink services. Simultaneous SPP/RFCOMM control and A2DP playback from the same Linux host has been validated.

## Prerequisites

On Debian/Ubuntu, the installer handles required packages. For manual installation:

```bash
sudo apt update
sudo apt install -y \
  bluetooth bluez bluez-tools \
  python3 python3-venv \
  pipewire pipewire-alsa pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils \
  shairport-sync sudo
sudo systemctl enable --now bluetooth
```

`pipewire-alsa` is required for the tested Shairport configuration. Verify that the dedicated audio user can see the PipeWire ALSA PCM:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID aplay -L
```

The output should include a `pipewire` PCM.

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

## Recommended installation

Clone the repository and run the guided installer:

```bash
git clone https://github.com/Relkci/ChromaComfort-Python-Control.git
cd ChromaComfort-Python-Control
sudo bash scripts/install-linux.sh
```

The installer asks for the Bluetooth MAC/RFCOMM channel, MQTT settings, Home Assistant names, and AirPlay speaker name. It installs the bridge under `/opt/chromacomfort`, writes private configuration to `/etc/chromacomfort/chromacomfort.conf`, creates the lingering `chromaudio` user, configures PipeWire/WirePlumber, installs Shairport Sync as a user service, generates built-in alert WAVs, and installs boot recovery services.

For an existing installation:

```bash
cd /opt/ChromaComfort-Python-Control
git pull
sudo bash scripts/update-linux.sh
```

The updater installs `pipewire-alsa` if necessary and migrates the managed Shairport configuration to the tested ALSA -> PipeWire backend. The first migration preserves the prior Shairport config as:

```text
/home/chromaudio/.config/shairport-sync/shairport-sync.conf.pre-alsa-migration
```

## Headless Bluetooth audio

A dedicated lingering user (`chromaudio` by default) owns the PipeWire/WirePlumber/Shairport audio session. The supplied WirePlumber configuration disables BlueZ seat monitoring and the normal Bluetooth sink suspend timeout for this dedicated headless use case.

Files are installed under:

```text
/home/chromaudio/.config/wireplumber/wireplumber.conf.d/
```

## Shairport Sync / Spotify volume

The tested Ubuntu Shairport Sync 4.3.7 package supports ALSA and PulseAudio (`pa`) but does not contain the newer native PipeWire backend. The stable tested path is therefore:

```text
AirPlay / Spotify
      |
      v
Shairport Sync 4.3.7
      |
      | ALSA backend
      v
pipewire-alsa PCM
      |
      v
PipeWire / WirePlumber
      |
      v
BlueZ A2DP
      |
      v
ChromaComfort-Sensonic Speaker
```

The working configuration is:

```conf
general =
{
    name = "Bathroom Speaker";
    output_backend = "alsa";
    audio_backend_buffer_desired_length_in_seconds = 0.5;
    disable_synchronization = "yes";
    ignore_volume_control = "no";
    volume_range_db = 30;
    volume_max_db = 0.0;
    volume_control_profile = "flat";
};

alsa =
{
    output_device = "pipewire";
};
```

Four settings/choices were important in testing:

- `output_backend = "alsa"` with `output_device = "pipewire"` avoids a reproducible pause/resume failure in the packaged Shairport `pa` backend.
- `audio_backend_buffer_desired_length_in_seconds = 0.5` eliminated choppy/clippy playback observed with the smaller default ALSA backend buffer on the tested Bluetooth path.
- `disable_synchronization = "yes"` avoids Shairport's normal resynchronization behavior fighting Bluetooth A2DP latency.
- `volume_range_db = 30` preserves AirPlay/Spotify volume control while avoiding the very large software attenuation range that made playback effectively inaudible at normal source-volume settings.

### Why the PulseAudio backend is not used

The original configuration used:

```conf
output_backend = "pa";
```

through `pipewire-pulse`. Initial playback and track skipping worked, but pausing Spotify/AirPlay caused the Shairport stream to enter:

```text
state: "idle"
pulse.corked = "true"
```

On resume, the stream remained corked and silent even though the AirPlay client reported playback. Restarting Shairport recreated the stream and restored audio. Repeated testing showed the transition:

```text
Playing: state=running, pulse.corked=false
Paused:  state=idle,    pulse.corked=true
Resume:  state=idle,    pulse.corked=true   <-- failed
```

Switching Shairport to ALSA through `pipewire-alsa` eliminated the tested pause/resume failure. For that reason the repository now treats ALSA -> PipeWire as the reference configuration rather than `pa` -> pipewire-pulse.

### Why the backend buffer is 0.5 seconds

After switching to ALSA -> PipeWire, playback resumed correctly after pauses but audio was audibly choppy and clipping-like. Increasing Shairport's desired backend buffer to:

```conf
audio_backend_buffer_desired_length_in_seconds = 0.5;
```

eliminated the observed dropouts on the reference host. This adds buffering latency, which is acceptable for the intended whole-room AirPlay use case, in exchange for stable playback through the ALSA -> PipeWire -> A2DP chain.

The distribution's system-level Shairport service remains disabled. Shairport runs as the dedicated audio user's systemd user service so it connects to the correct PipeWire runtime.

## Why the boot recovery services exist

### BlueZ can be unresponsive after boot

`chromacomfort-bluetooth-ready.service` runs a bounded `bluetoothctl show` health check. It waits for normal initialization and only restarts `bluetooth.service` if BlueZ remains unresponsive.

### Bluetooth Connected does not guarantee A2DP

The RFCOMM/SPP control session can make the device report `Connected: yes` while PipeWire still has no `bluez_output...` A2DP sink. `chromacomfort-audio-ready.service` checks the actual audio sink. If missing, it disconnects/reconnects the device and retries until WirePlumber creates it.

After the A2DP sink is confirmed, the readiness service restarts Shairport Sync. This avoids the boot race where Shairport could start before the Bluetooth sink existed and appear functional while producing no audio.

The readiness check still uses the PulseAudio-compatible `pactl` interface to query PipeWire sinks. This is independent of Shairport's playback backend; Shairport itself uses ALSA -> PipeWire.

## Home Assistant alert sounds

Home Assistant can publish a built-in sound name to:

```text
<topic_prefix>/audio/play
```

The audio helper plays local WAV files directly with `pw-play --target <ChromaComfort sink>`. This path is native PipeWire and does not depend on Shairport's selected backend. If AirPlay is active, PipeWire can mix the alert with it.

## Service checks

One-command overview:

```bash
sudo chromacomfort-status
```

PipeWire/WirePlumber:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID wpctl status
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pactl list short sinks
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID aplay -L
```

A healthy installation should have the stable A2DP sink:

```text
bluez_output.AA_BB_CC_DD_EE_FF.1
```

and an ALSA PCM named `pipewire`.

Shairport user service:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio \
  XDG_RUNTIME_DIR=/run/user/$CHROMA_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$CHROMA_UID/bus \
  systemctl --user status shairport-sync.service --no-pager
```

Confirm the installed Shairport build and managed backend/buffer:

```bash
shairport-sync -V
grep -nE 'output_backend|audio_backend_buffer|alsa|pa|pipewire' /home/chromaudio/.config/shairport-sync/shairport-sync.conf
```

## Pause/resume and audio-quality validation

After installation or update:

1. Start Spotify/AirPlay playback and confirm clean, continuous audio.
2. Skip to another track and confirm playback continues.
3. Pause playback for at least several seconds.
4. Resume playback and confirm audio returns without restarting Shairport.
5. Repeat pause/resume several times.
6. Listen for choppy, clipping-like, or dropout behavior during sustained playback.

With the reference ALSA -> PipeWire configuration and 0.5-second backend buffer, both pause/resume and sustained playback quality have been validated successfully on the development host.

## Reboot validation

After installation, reboot and verify without manual intervention:

1. Home Assistant retains MQTT device state.
2. Fan/light/RGB controls physically work.
3. `wpctl status` shows the ChromaComfort Bluetooth device and sink.
4. The configured AirPlay speaker appears.
5. Spotify/AirPlay playback is audible and clean.
6. Source volume control works.
7. Spotify/AirPlay pause and resume restores audio without a Shairport restart.
8. Sustained playback remains free of choppy/dropout artifacts.
9. Publishing a built-in alert to `<topic_prefix>/audio/play` produces an audible sound.
10. An alert can mix with active AirPlay playback.

## Protocol/testing notes

1. The daemon assumes the ChromaComfort is paired and trusted by BlueZ.
2. The tested unit uses Bluetooth Classic SPP/RFCOMM channel 7.
3. Fan, white light, wall RGB, white brightness, RGB Favorite Color, and RGB brightness have been validated from Linux through Home Assistant/MQTT.
4. Incoming status includes current brightness but does not appear to expose selected RGB channel values, so the bridge remembers successfully commanded RGB values.
5. Bluetooth numeric PipeWire/WirePlumber object IDs are ephemeral. Scripts use the stable `bluez_output.<MAC>.1` name instead.
6. RFCOMM control and A2DP audio are kept as separate services intentionally.
7. Local alert playback uses native PipeWire through `pw-play`; Shairport playback uses ALSA through PipeWire.

## Acknowledgement

The protocol work builds on Taylor Finnell's ChromaComfort reverse engineering:

https://gist.github.com/taylorfinnell/5349b8085d57836a45be7637055e0692
