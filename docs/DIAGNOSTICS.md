# ChromaComfort diagnostics

The Linux installer provides a one-command health snapshot:

```bash
chromacomfort-status
```

It reports BlueZ, ChromaComfort readiness/control services, PipeWire/WirePlumber, Shairport, Bluetooth pairing/connection state, RFCOMM, the audio topology, the expected A2DP sink, and recent journals.

## Audio-user commands

PipeWire runs in the dedicated `chromaudio` user session, so run diagnostic commands in that runtime rather than as root:

```bash
CHROMA_UID=$(id -u chromaudio)
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID wpctl status
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pactl list short sinks
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID aplay -L
```

`aplay -L` should include a `pipewire` PCM. If it does not, verify that `pipewire-alsa` is installed.

The stable A2DP sink name is derived from the Bluetooth MAC:

```text
bluez_output.AA_BB_CC_DD_EE_FF.1
```

Do not rely on numeric `wpctl` node IDs because they change between sessions and reboots.

## Shairport backend

The reference configuration uses:

```text
Shairport ALSA -> pipewire-alsa -> PipeWire -> BlueZ A2DP
```

Verify:

```bash
grep -nE 'output_backend|alsa|pa|pipewire' /home/chromaudio/.config/shairport-sync/shairport-sync.conf
```

Expected:

```text
output_backend = "alsa";
output_device = "pipewire";
```

## Historical pause/resume failure

The previous Shairport `pa` backend was found to remain corked after an AirPlay/Spotify pause. When diagnosing an older installation still using `output_backend = "pa"`, first identify the Shairport node from `wpctl status`, then inspect it in the `chromaudio` runtime:

```bash
sudo -u chromaudio XDG_RUNTIME_DIR=/run/user/$CHROMA_UID pw-cli info <NODE_ID> | grep -E 'state:|pulse.corked'
```

The reproduced failure was:

```text
playing: state="running", pulse.corked="false"
paused:  state="idle",    pulse.corked="true"
resume:  state="idle",    pulse.corked="true"
```

Restarting Shairport cleared the stuck stream, but the permanent tested fix is to install `pipewire-alsa` and use Shairport's ALSA backend with `output_device = "pipewire"`.

Existing repository-managed installations can migrate with:

```bash
cd /opt/ChromaComfort-Python-Control
git pull
sudo bash scripts/update-linux.sh
```

The updater preserves the original Shairport config once as `shairport-sync.conf.pre-alsa-migration`.

## Pause/resume regression test

After any audio-stack change:

1. Start AirPlay/Spotify and confirm audio.
2. Skip tracks and confirm playback continues.
3. Pause for several seconds.
4. Resume and confirm audio returns.
5. Repeat pause/resume multiple times without restarting Shairport.

This test specifically covers the failure that motivated the ALSA -> PipeWire backend change.
