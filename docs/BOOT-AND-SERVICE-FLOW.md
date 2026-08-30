# Boot and Service Flow

This document describes the expected ChromaComfort Linux bridge startup sequence, what each service is responsible for, and when Home Assistant control, Home Assistant alert playback, and AirPlay should be considered ready.

The important design principle is that control and audio are separate readiness paths. A problem with AirPlay should not prevent Home Assistant fan/light control, and alert playback does not require Shairport Sync once the A2DP/PipeWire path is ready.

## High-level boot flow

```text
                           LINUX BOOT
                               |
                               v
                    systemd + networking
                               |
                               v
                     bluetooth.service
                         BlueZ starts
                               |
                               v
           chromacomfort-bluetooth-ready.service
                               |
                    Is BlueZ responsive?
                      /              \
                    no                yes
                    |                  |
             restart BlueZ            |
                    \__________________/
                               |
                        Bluetooth Ready
                               |
              +----------------+----------------+
              |                                 |
              v                                 v
       CONTROL / MQTT PATH                 AUDIO PATH
              |                                 |
              |                         chromaudio user session
              |                                 |
              |                         PipeWire / pipewire-pulse
              |                                 |
              |                             WirePlumber
              |                                 |
              v                                 v
  chromacomfort.service             chromacomfort-audio-ready.service
              |                                 |
              v                                 v
    rfcomm connect channel             Check actual A2DP sink
              |                                 |
              v                          Sink missing?
        /dev/rfcomm0                            |
              |                                 v
              v                       disconnect/connect Bluetooth
   Bluetooth serial link                        |
              |                                 v
              v                         WirePlumber creates
       Device status RX                bluez_output.<MAC>.1
              |                                 |
              v                                 v
       MQTT connection                       A2DP READY
              |                                 |
              v                                 +------> HA ALERT AUDIO PATH READY
      HA MQTT Discovery                        |
              |                                 v
              v                         Restart Shairport Sync
   HOME ASSISTANT CONTROL                      |
          READY                                v
                                      Shairport uses correct
                                        PipeWire A2DP sink
                                                |
                                                v
                                          AIRPLAY READY
                                                |
                                                v
                                chromacomfort-audio-status.service
                                                |
                                                v
                                   MQTT audio diagnostics in HA
```

## Readiness milestones

| Milestone | Meaning |
| --- | --- |
| **BlueZ Ready** | The Linux Bluetooth stack has passed the bounded health check and is responsive. |
| **Control Bluetooth Ready** | RFCOMM/SPP is connected and `/dev/rfcomm0` is usable by the control bridge. |
| **Home Assistant Control Ready** | MQTT and RFCOMM are working, device status is flowing, and fan/light/RGB controls are usable in Home Assistant. |
| **A2DP Ready** | The `chromaudio` PipeWire session contains the real ChromaComfort `bluez_output.<MAC>.1` sink. |
| **Home Assistant Alerts Ready** | MQTT audio helper is running and can play local WAV alerts through PipeWire/A2DP. |
| **AirPlay Ready** | Shairport Sync has been started/restarted after A2DP readiness and is attached to the correct PipeWire output. |
| **Fully Ready** | Home Assistant controls, Home Assistant alerts, and AirPlay are all operational. |

These milestones do not necessarily happen at exactly the same time. Home Assistant fan/light control can become usable before the audio path is ready.

## 1. Linux, systemd, and networking

Normal operating-system services start first. Networking, D-Bus, systemd user sessions, and Bluetooth infrastructure begin coming online.

The ChromaComfort services use systemd dependencies and their own readiness checks rather than assuming that a service being marked `active` means the underlying hardware path is usable.

## 2. `bluetooth.service`

BlueZ starts and initializes the Bluetooth controller.

At this point the controller may exist without being fully responsive. During testing, BlueZ occasionally entered a state after boot where even `bluetoothctl show` could hang.

For that reason, later ChromaComfort services do not treat `bluetooth.service active` by itself as proof that Bluetooth is ready.

## 3. `chromacomfort-bluetooth-ready.service`

This is the first ChromaComfort-specific readiness gate.

The service runs the bounded BlueZ readiness helper. It checks whether `bluetoothctl` can communicate with BlueZ. If BlueZ remains unresponsive after the initial attempts, the helper restarts `bluetooth.service` and waits for it again.

When this service succeeds:

```text
BlueZ Ready
```

The control and audio paths can then progress independently.

Useful checks:

```bash
systemctl status chromacomfort-bluetooth-ready --no-pager
journalctl -u chromacomfort-bluetooth-ready -b --no-pager
```

## 4A. Control and MQTT path

### `chromacomfort.service`

This service runs `chromacomfort_bridge.py`.

Its responsibilities include:

1. Connect to the configured MQTT broker.
2. Launch the native BlueZ `rfcomm connect` process.
3. Establish the configured RFCOMM Serial Port Profile channel to the ChromaComfort.
4. Wait for `/dev/rfcomm0`.
5. Open `/dev/rfcomm0` with pyserial.
6. Receive ChromaComfort status packets.
7. Publish state and MQTT Discovery information.
8. Receive Home Assistant commands and retransmit ChromaComfort commands until the expected acknowledgement is received.
9. Reconnect automatically if the RFCOMM connection is lost.

Once MQTT and RFCOMM are communicating and device state is flowing:

```text
HOME ASSISTANT CONTROL READY
```

At this point Home Assistant should be able to control the fan, white light, RGB light, RGB brightness/color, and wall RGB mode even if the audio path is still initializing.

Useful checks:

```bash
systemctl status chromacomfort --no-pager
journalctl -u chromacomfort -b --no-pager
```

## 4B. `chromaudio` user audio session

Audio runs in a dedicated lingering Linux user named `chromaudio` by default.

Lingering allows the user's systemd/PipeWire environment to exist after boot without an interactive desktop login.

The relevant user services are:

```text
PipeWire
pipewire-pulse
WirePlumber
Shairport Sync
```

PipeWire provides the audio graph. `pipewire-pulse` provides PulseAudio compatibility used by the packaged Shairport Sync build. WirePlumber manages devices and routing.

The supplied WirePlumber configuration disables normal active-seat restrictions for Bluetooth audio so a headless machine can create and manage the ChromaComfort A2DP device.

Useful check:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID wpctl status
```

## 5. `chromacomfort-audio-ready.service`

This service determines whether Bluetooth **audio** is actually ready.

A generic Bluetooth status of:

```text
Connected: yes
```

is not sufficient. The RFCOMM control connection can make the ChromaComfort appear connected while no A2DP audio sink exists.

The readiness service therefore checks the `chromaudio` PipeWire/PulseAudio environment for the stable sink name:

```text
bluez_output.<CHROMACOMFORT_MAC>.1
```

For example, the numeric WirePlumber object ID may change every boot, but the `bluez_output.<MAC>.1` name is stable and is what the scripts use.

### If the A2DP sink is missing

The service performs a Bluetooth disconnect/reconnect and retries. This is intentional. Testing showed that the first reconnect can occasionally return a Bluetooth page timeout while a subsequent attempt succeeds.

Once WirePlumber creates the actual ChromaComfort A2DP sink:

```text
A2DP READY
```

This is the key audio transport milestone.

Useful checks:

```bash
systemctl status chromacomfort-audio-ready --no-pager
journalctl -u chromacomfort-audio-ready -b --no-pager

CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pactl list short sinks
```

## 6. Shairport Sync and AirPlay readiness

A boot race was reproduced where Shairport Sync started before the ChromaComfort A2DP sink existed.

The failure looked like this:

```text
Shairport starts
      |
      v
A2DP sink does not exist yet
      |
      v
Shairport initializes against the wrong/incomplete audio environment
      |
      v
A2DP eventually appears
      |
      v
AirPlay appears available but playback is silent
```

Manually restarting Shairport after the A2DP sink appeared immediately restored audio, confirming the ordering problem.

The permanent startup sequence is therefore:

```text
A2DP sink confirmed
      |
      v
restart Shairport Sync
      |
      v
Shairport sees the correct PipeWire environment
      |
      v
Shairport routes to ChromaComfort A2DP
      |
      v
AIRPLAY READY
```

The audio-readiness helper deliberately performs the Shairport restart only after confirming that the ChromaComfort A2DP sink exists.

At this point the configured AirPlay destination, such as `Bathroom Speaker`, should be both visible and capable of producing sound.

Useful check:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio \
  XDG_RUNTIME_DIR=/run/user/$CHROMA_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$CHROMA_UID/bus \
  systemctl --user status shairport-sync.service --no-pager
```

## 7. `chromacomfort-audio-status.service`

This service runs `chromacomfort_audio_status.py`.

It performs two related jobs:

### Audio diagnostics

It monitors the audio environment and publishes MQTT/Home Assistant diagnostics including:

- Audio Status
- A2DP Sink
- AirPlay Service
- AirPlay Stream
- Audio Output
- Last Alert

A healthy idle system should generally show:

```text
Audio Status: Ready
A2DP Sink: Connected / ON
AirPlay Service: Connected / ON
AirPlay Stream: Idle
Audio Output: ChromaComfort-Sensonic Speaker
```

`AirPlay Stream: Idle` is healthy. It means Shairport is available but no AirPlay client is currently streaming.

### Home Assistant alert playback

The same helper subscribes to:

```text
<topic_prefix>/audio/play
```

When Home Assistant publishes a built-in sound name such as `doorbell` or `complete`, the helper uses `pw-play` to play the local WAV directly through the ChromaComfort A2DP sink.

This path is:

```text
Home Assistant
      |
      v
MQTT audio/play
      |
      v
chromacomfort_audio_status.py
      |
      v
pw-play
      |
      v
PipeWire
      |
      v
ChromaComfort A2DP sink
      |
      v
Speaker
```

Shairport is not paused, stopped, or restarted to play alerts. PipeWire mixes the alert with an existing AirPlay stream.

Therefore, alert playback does not depend on an active AirPlay stream. In practical terms, Home Assistant alerts are ready when all of the following are true:

```text
MQTT connected
chromacomfort-audio-status.service running
PipeWire audio session running
ChromaComfort A2DP sink present
```

## Home Assistant readiness

There are two different Home Assistant readiness points.

### Home Assistant device control ready

The device's normal fan/light controls are ready when:

```text
MQTT connected
+
RFCOMM connected
+
ChromaComfort status/ACK traffic working
```

This can happen before audio is ready.

### Home Assistant audio alerts ready

Alert playback is ready when:

```text
MQTT audio helper connected
+
PipeWire running
+
ChromaComfort A2DP sink present
```

Shairport does not need to be actively streaming for an alert to play.

## Fully ready state

A normal fully initialized system looks conceptually like this:

```text
ChromaComfort
|
+-- Control
|   +-- Fan                         READY
|   +-- White Light                 READY
|   +-- RGB Light                   READY
|   +-- Wall RGB Mode               READY
|
+-- Audio
    +-- A2DP Sink                   READY
    +-- HA Alert Playback           READY
    +-- AirPlay Service             READY
    +-- AirPlay Stream              Idle (healthy)
    +-- Audio Output                ChromaComfort
```

## Why the services are separated

The services are intentionally divided by responsibility:

| Service | Responsibility |
| --- | --- |
| `bluetooth.service` | Linux BlueZ Bluetooth stack. |
| `chromacomfort-bluetooth-ready.service` | Verify BlueZ is actually responsive and recover it if necessary. |
| `chromacomfort.service` | RFCOMM/SPP control protocol, MQTT, Home Assistant discovery/state/commands. |
| `user@<chromaudio UID>.service` | Hosts the dedicated lingering user's audio services. |
| PipeWire / `pipewire-pulse` | Audio graph and PulseAudio compatibility. |
| WirePlumber | Bluetooth audio device creation, policy, and routing. |
| `chromacomfort-audio-ready.service` | Verify the real A2DP sink exists, recover Bluetooth audio if needed, then restart Shairport in the correct order. |
| `shairport-sync.service` (user service) | AirPlay receiver feeding audio into PipeWire. |
| `chromacomfort-audio-status.service` | MQTT audio diagnostics and Home Assistant-triggered local WAV playback. |

This separation prevents one failure from unnecessarily taking down unrelated functions. For example, Shairport can fail while Home Assistant fan/light control remains operational.

## One-command troubleshooting

Start with:

```bash
sudo chromacomfort-status
```

The diagnostic script summarizes system services, the RFCOMM link, Bluetooth device information, PipeWire/WirePlumber state, A2DP sink presence, Shairport state, and recent logs.

When troubleshooting a boot failure, identify the first readiness milestone that did not complete:

```text
BlueZ Ready?
   |
   +-- no  -> inspect bluetooth + bluetooth-ready
   |
   yes
   |
   +-- HA control missing -> inspect chromacomfort.service / RFCOMM / MQTT
   |
   +-- A2DP missing -> inspect PipeWire, WirePlumber, audio-ready
   |
   +-- A2DP present but AirPlay silent -> inspect Shairport routing/start order
   |
   +-- AirPlay works but HA alerts fail -> inspect audio-status MQTT listener and pw-play
```

This approach is usually more useful than treating `Bluetooth Connected: yes` as proof that the entire stack is healthy.

## Expected reboot validation

After a reboot, verify the following without manually restarting anything:

1. `chromacomfort-bluetooth-ready.service` completes successfully.
2. Home Assistant receives ChromaComfort MQTT state.
3. Fan/light/RGB controls physically work.
4. `wpctl status` shows the ChromaComfort `[bluez5]` device and sink.
5. Audio Status reports `Ready` in Home Assistant.
6. The configured AirPlay speaker appears.
7. AirPlay/Spotify playback is audible.
8. Source volume control changes output level.
9. A Home Assistant `audio/play` command produces a built-in alert sound.
10. An alert can mix with an active AirPlay stream without stopping or restarting Shairport.

If all ten succeed, the control, alert, and AirPlay paths have all reached the expected fully-ready state.
