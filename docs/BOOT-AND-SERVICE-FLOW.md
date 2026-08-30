# Boot and Service Flow

This document describes the expected ChromaComfort Linux bridge startup sequence and the independent control and audio readiness paths.

## High-level boot flow

```text
                           LINUX BOOT
                               |
                               v
                     bluetooth.service
                               |
                               v
           chromacomfort-bluetooth-ready.service
                               |
                        Bluetooth Ready
                               |
              +----------------+----------------+
              |                                 |
              v                                 v
       CONTROL / MQTT PATH                 AUDIO PATH
              |                                 |
      chromacomfort.service              chromaudio session
              |                                 |
       RFCOMM /dev/rfcomm0              PipeWire + WirePlumber
              |                                 |
              v                                 v
      MQTT / HA CONTROL             chromacomfort-audio-ready
            READY                              |
                                         Check A2DP sink
                                                |
                                      reconnect if missing
                                                |
                                                v
                                      bluez_output.<MAC>.1
                                                |
                                                v
                                           A2DP READY
                                                |
                                   +------------+------------+
                                   |                         |
                                   v                         v
                            HA alert audio             restart Shairport
                              via pw-play                    |
                                                           v
                                                ALSA -> pipewire-alsa
                                                           |
                                                           v
                                                        PipeWire
                                                           |
                                                           v
                                                    BlueZ A2DP sink
                                                           |
                                                           v
                                                      AIRPLAY READY
```

## Readiness milestones

| Milestone | Meaning |
| --- | --- |
| **BlueZ Ready** | Bluetooth stack is responsive. |
| **Home Assistant Control Ready** | MQTT and RFCOMM are working and device state is flowing. |
| **A2DP Ready** | The `chromaudio` PipeWire session contains the ChromaComfort `bluez_output.<MAC>.1` sink. |
| **Home Assistant Alerts Ready** | MQTT audio helper can use `pw-play` against the A2DP sink. |
| **AirPlay Ready** | Shairport has been restarted after A2DP readiness and can feed PipeWire through its ALSA backend. |
| **Fully Ready** | Control, alerts, and AirPlay are operational. |

## Dedicated audio session

Audio runs as the lingering `chromaudio` user. Relevant components are:

```text
PipeWire
pipewire-alsa
pipewire-pulse
WirePlumber
Shairport Sync
```

PipeWire owns the audio graph and WirePlumber owns routing/device policy. `pipewire-alsa` exposes the `pipewire` ALSA PCM used by Shairport. `pipewire-pulse` remains installed because diagnostics and readiness checks use the PulseAudio-compatible `pactl` interface to query PipeWire sinks; Shairport playback itself no longer uses the `pa` backend.

## A2DP readiness

`Bluetooth Connected: yes` is not sufficient because RFCOMM can be connected while the A2DP profile is missing. `chromacomfort-audio-ready.service` therefore checks for:

```text
bluez_output.<CHROMACOMFORT_MAC>.1
```

If missing, the service performs bounded Bluetooth disconnect/connect retries until WirePlumber creates the A2DP sink.

Once the sink exists, the service restarts Shairport Sync so its playback path is initialized only after the real Bluetooth audio output is available.

## Shairport playback path

The tested Shairport Sync 4.3.7 package contains ALSA and PulseAudio (`pa`) backends but not the native PipeWire backend. The reference path is:

```text
AirPlay
  |
  v
Shairport Sync
  |
  | output_backend = "alsa"
  v
ALSA PCM "pipewire"
  |
  v
pipewire-alsa
  |
  v
PipeWire / WirePlumber
  |
  v
bluez_output.<MAC>.1
  |
  v
ChromaComfort
```

### Why ALSA is used instead of Shairport's `pa` backend

The original path was:

```text
Shairport pa -> pipewire-pulse -> PipeWire -> A2DP
```

It produced a repeatable failure after pausing AirPlay/Spotify. While playing, the Shairport node showed:

```text
state: "running"
pulse.corked = "false"
```

After pause:

```text
state: "idle"
pulse.corked = "true"
```

When playback was resumed, the client reported playing but the node remained:

```text
state: "idle"
pulse.corked = "true"
```

and no audio returned. Restarting Shairport recreated the stream and temporarily restored playback. Track skipping did not reproduce the failure; pause/resume did.

Changing Shairport to:

```conf
output_backend = "alsa";

alsa =
{
    output_device = "pipewire";
};
```

bypassed the stuck PulseAudio cork state and restored reliable pause/resume in testing. New installations use this path, and `update-linux.sh` migrates existing managed installations to it.

## Home Assistant alert playback

Alerts do not use Shairport. `chromacomfort_audio_status.py` receives an MQTT sound name and executes `pw-play --target <A2DP sink>`, feeding PipeWire directly. Therefore alerts can remain functional even if Shairport itself has a problem.

## Service responsibilities

| Service/component | Responsibility |
| --- | --- |
| `bluetooth.service` | BlueZ Bluetooth stack. |
| `chromacomfort-bluetooth-ready.service` | Verify/recover BlueZ responsiveness. |
| `chromacomfort.service` | RFCOMM/SPP control, MQTT, Home Assistant discovery/state/commands. |
| `user@<UID>.service` | Hosts the lingering `chromaudio` user session. |
| PipeWire / `pipewire-alsa` | Audio graph and ALSA compatibility used by Shairport. |
| `pipewire-pulse` | PulseAudio compatibility retained for diagnostics/readiness queries. |
| WirePlumber | Bluetooth audio device creation, policy, and routing. |
| `chromacomfort-audio-ready.service` | Verify/recover the A2DP sink, then restart Shairport. |
| `shairport-sync.service` | AirPlay receiver using ALSA -> PipeWire. |
| `chromacomfort-audio-status.service` | MQTT audio diagnostics and direct PipeWire alert playback. |

## One-command troubleshooting

Start with:

```bash
sudo chromacomfort-status
```

For the audio user:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID wpctl status
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pactl list short sinks
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID aplay -L
```

The final command should include a `pipewire` PCM. Numeric `wpctl` node IDs are ephemeral and should not be used in scripts.

Confirm Shairport's managed configuration:

```bash
grep -nE 'output_backend|alsa|pa|pipewire' /home/chromaudio/.config/shairport-sync/shairport-sync.conf
```

Expected values include:

```text
output_backend = "alsa";
output_device = "pipewire";
```

## Expected reboot and playback validation

After a reboot, verify without manual intervention:

1. Bluetooth readiness completes.
2. Home Assistant receives MQTT state and fan/light/RGB controls work.
3. `wpctl status` shows the ChromaComfort Bluetooth device and A2DP sink.
4. `aplay -L` as `chromaudio` includes the `pipewire` PCM.
5. Audio Status reports `Ready`.
6. The configured AirPlay speaker appears.
7. AirPlay/Spotify playback is audible.
8. Track skipping continues playback.
9. Pause for several seconds, resume, and verify audio returns without restarting Shairport.
10. Repeat pause/resume several times.
11. A Home Assistant `audio/play` command produces a built-in alert.
12. An alert can mix with active AirPlay playback.

If these succeed, the control, alert, startup-recovery, and pause/resume paths have reached the expected state.
