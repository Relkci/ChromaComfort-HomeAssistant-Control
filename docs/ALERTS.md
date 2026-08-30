# Home Assistant alert sounds

The ChromaComfort audio helper can play short local WAV alerts through the same PipeWire A2DP sink used by Shairport Sync. Playback is mixed by PipeWire, so alerts do not pause, stop, or restart AirPlay.

## Built-in sounds

The installer generates these WAV files locally under `/opt/chromacomfort/sounds/`:

- `doorbell.wav`
- `complete.wav`
- `alert.wav`
- `notification.wav`

The sounds are synthesized by `scripts/generate-alert-sounds.py`. No third-party audio files or external downloads are required.

## MQTT topic

Publish the sound name to:

```text
<topic_prefix>/audio/play
```

With the default topic prefix, examples are:

```bash
mosquitto_pub -h MQTT_BROKER -t chromacomfort/bathroom/audio/play -m doorbell
mosquitto_pub -h MQTT_BROKER -t chromacomfort/bathroom/audio/play -m complete
```

Only simple local sound names are accepted. The helper resolves them to `<sounds_dir>/<name>.wav`; arbitrary paths and URLs are intentionally not accepted.

## Home Assistant automation example

```yaml
action:
  - action: mqtt.publish
    data:
      topic: chromacomfort/bathroom/audio/play
      payload: doorbell
```

For a washing-machine-complete automation:

```yaml
action:
  - action: mqtt.publish
    data:
      topic: chromacomfort/bathroom/audio/play
      payload: complete
```

The helper also publishes the most recent alert state to:

```text
<topic_prefix>/audio/last_alert
```

and exposes it through MQTT Discovery as the `Last Alert` diagnostic entity.

## Mixing behavior

The alert is played with `pw-play` directly to the stable ChromaComfort A2DP sink. Shairport Sync is not paused, stopped, restarted, or otherwise controlled during alert playback. If AirPlay is already playing, PipeWire mixes the alert into the existing audio stream.

Alert requests are serialized so multiple rapid MQTT messages do not create overlapping alert sounds.
