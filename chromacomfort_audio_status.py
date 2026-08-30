#!/usr/bin/env python3
"""Publish ChromaComfort AirPlay/A2DP health to MQTT/Home Assistant."""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

LOG = logging.getLogger("chromacomfort-audio-status")


def run_command(args: list[str], *, user: str | None = None, uid: int | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    command = args
    if user is not None and uid is not None:
        runtime = f"/run/user/{uid}"
        env.update({
            "XDG_RUNTIME_DIR": runtime,
            "PULSE_SERVER": f"unix:{runtime}/pulse/native",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        })
        command = ["sudo", "-u", user, "env"] + [f"{k}={v}" for k, v in env.items() if k in {
            "XDG_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS"
        }] + args

    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


@dataclass
class Settings:
    bluetooth_address: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_topic: str
    discovery_prefix: str
    device_name: str
    device_id: str

    @classmethod
    def load(cls, path: str) -> "Settings":
        cfg = configparser.ConfigParser()
        if not cfg.read(path):
            raise FileNotFoundError(f"Unable to read config: {path}")
        return cls(
            bluetooth_address=cfg.get("bluetooth", "address"),
            mqtt_host=cfg.get("mqtt", "host"),
            mqtt_port=cfg.getint("mqtt", "port", fallback=1883),
            mqtt_username=cfg.get("mqtt", "username", fallback=""),
            mqtt_password=cfg.get("mqtt", "password", fallback=""),
            mqtt_topic=cfg.get("mqtt", "topic_prefix", fallback="chromacomfort/bathroom").rstrip("/"),
            discovery_prefix=cfg.get("mqtt", "discovery_prefix", fallback="homeassistant").rstrip("/"),
            device_name=cfg.get("device", "name", fallback="ChromaComfort"),
            device_id=cfg.get("device", "id", fallback="chromacomfort_bathroom"),
        )


class AudioStatusPublisher:
    def __init__(self, settings: Settings, audio_user: str, interval: int):
        self.s = settings
        self.audio_user = audio_user
        self.audio_uid = int(subprocess.check_output(["id", "-u", audio_user], text=True).strip())
        self.interval = interval
        self.expected_sink = f"bluez_output.{self.s.bluetooth_address.replace(':', '_')}.1"

        try:
            self.mqtt = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"{self.s.device_id}_audio_status",
            )
        except AttributeError:
            self.mqtt = mqtt.Client(client_id=f"{self.s.device_id}_audio_status")

        if self.s.mqtt_username:
            self.mqtt.username_pw_set(self.s.mqtt_username, self.s.mqtt_password)
        self.mqtt.will_set(self.topic("audio/availability"), "offline", retain=True)
        self.mqtt.on_connect = self._on_connect

    def topic(self, suffix: str) -> str:
        return f"{self.s.mqtt_topic}/{suffix}"

    def publish(self, suffix: str, value: str) -> None:
        self.mqtt.publish(self.topic(suffix), value, qos=0, retain=True)

    def device(self) -> dict:
        return {
            "identifiers": [self.s.device_id],
            "name": self.s.device_name,
            "manufacturer": "Broan-NuTone",
            "model": "ChromaComfort Sensonic",
        }

    def discovery(self, component: str, object_id: str, config: dict) -> None:
        config["unique_id"] = f"{self.s.device_id}_{object_id}"
        config["device"] = self.device()
        config["availability_topic"] = self.topic("audio/availability")
        config["payload_available"] = "online"
        config["payload_not_available"] = "offline"
        config["entity_category"] = "diagnostic"
        topic = f"{self.s.discovery_prefix}/{component}/{self.s.device_id}/{object_id}/config"
        self.mqtt.publish(topic, json.dumps(config), qos=0, retain=True)

    def publish_discovery(self) -> None:
        self.discovery("sensor", "audio_status", {
            "name": "Audio Status",
            "state_topic": self.topic("audio/status"),
            "icon": "mdi:speaker-wireless",
        })
        self.discovery("binary_sensor", "a2dp_sink", {
            "name": "A2DP Sink",
            "state_topic": self.topic("audio/a2dp_sink"),
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "connectivity",
        })
        self.discovery("binary_sensor", "airplay_service", {
            "name": "AirPlay Service",
            "state_topic": self.topic("audio/airplay_service"),
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "running",
        })
        self.discovery("sensor", "airplay_stream", {
            "name": "AirPlay Stream",
            "state_topic": self.topic("audio/stream"),
            "icon": "mdi:cast-audio",
        })
        self.discovery("sensor", "audio_output", {
            "name": "Audio Output",
            "state_topic": self.topic("audio/output"),
            "icon": "mdi:speaker",
        })

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        LOG.info("MQTT connected")
        self.publish("audio/availability", "online")
        self.publish_discovery()
        self.publish_status()

    def get_status(self) -> dict[str, str]:
        rc, sinks = run_command(["pactl", "list", "short", "sinks"], user=self.audio_user, uid=self.audio_uid)
        sink_present = rc == 0 and self.expected_sink in sinks

        rc, service = run_command(
            ["systemctl", "--user", "is-active", "shairport-sync.service"],
            user=self.audio_user,
            uid=self.audio_uid,
        )
        shairport_active = rc == 0 and service.strip() == "active"

        rc, wpctl = run_command(["wpctl", "status"], user=self.audio_user, uid=self.audio_uid)
        routed = False
        paused = False
        output = "Unknown"
        if rc == 0:
            lines = wpctl.splitlines()
            in_streams = False
            stream_seen = False
            for line in lines:
                stripped = line.strip()
                if "Streams:" in stripped:
                    in_streams = True
                    stream_seen = False
                    continue
                if in_streams and stripped.startswith("Video"):
                    break
                if not in_streams:
                    continue
                if "Bathroom Speaker" in line or "shairport" in line.lower():
                    stream_seen = True
                if stream_seen and ">" in line:
                    right = line.split(">", 1)[1].strip()
                    output = right.split(":playback", 1)[0].strip()
                    if "ChromaComfort-Sensonic Speaker" in right:
                        routed = True
                    if "[paused]" in right.lower():
                        paused = True

        if not shairport_active:
            stream_state = "Stopped"
        elif routed and paused:
            stream_state = "Paused"
        elif routed:
            stream_state = "Playing"
        else:
            stream_state = "Idle"

        if not shairport_active:
            overall = "Shairport Down"
        elif not sink_present:
            overall = "A2DP Missing"
        elif routed and output != "Unknown" and "ChromaComfort-Sensonic Speaker" not in output:
            overall = "Wrong Output"
        else:
            overall = "Ready"

        if output == "Unknown" and sink_present:
            output = "ChromaComfort-Sensonic Speaker"

        return {
            "status": overall,
            "a2dp_sink": "ON" if sink_present else "OFF",
            "airplay_service": "ON" if shairport_active else "OFF",
            "stream": stream_state,
            "output": output,
        }

    def publish_status(self) -> None:
        status = self.get_status()
        for key, value in status.items():
            self.publish(f"audio/{key}", value)
        LOG.info(
            "Audio status=%s A2DP=%s AirPlay=%s stream=%s output=%s",
            status["status"],
            status["a2dp_sink"],
            status["airplay_service"],
            status["stream"],
            status["output"],
        )

    def run(self) -> None:
        self.mqtt.connect(self.s.mqtt_host, self.s.mqtt_port, keepalive=60)
        self.mqtt.loop_start()
        try:
            while True:
                self.publish_status()
                time.sleep(self.interval)
        finally:
            self.publish("audio/availability", "offline")
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish ChromaComfort AirPlay/A2DP status to MQTT")
    parser.add_argument("--config", default="/etc/chromacomfort/chromacomfort.conf")
    parser.add_argument("--audio-user", default="chromaudio")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings.load(args.config)
    AudioStatusPublisher(settings, args.audio_user, max(5, args.interval)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
