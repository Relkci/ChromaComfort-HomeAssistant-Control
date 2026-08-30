#!/usr/bin/env python3
"""ChromaComfort Linux RFCOMM/SPP to MQTT/Home Assistant bridge.

Linux transport note:
Direct Python AF_BLUETOOTH RFCOMM connections timed out against the tested
ChromaComfort unit, while BlueZ's `rfcomm connect` plus pyserial was proven to
work reliably. This daemon therefore manages `rfcomm connect` as a subprocess,
opens the resulting /dev/rfcomm device with pyserial, and reconnects it when the
session drops.
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt
import serial

START = 0x3A
PAYLOAD_LEN = 17

CMD_FAN_ON = 0x01
CMD_FAN_OFF = 0x02
CMD_WHITE_ON = 0x03
CMD_WHITE_OFF = 0x04
CMD_WALL_RGB_ON = 0x05
CMD_WALL_RGB_OFF = 0x06
CMD_ACTIVATE_FAVORITE1 = 0x0B
CMD_DEACTIVATE_FAVORITE1 = 0x0C
CMD_SAVE_FAVORITE1 = 0x0D

STATUS_FAN = 0x80
STATUS_WHITE = 0x40
STATUS_WALL_RGB = 0x20
STATUS_SWEEP = 0x10
STATUS_FAVORITE1 = 0x08
STATUS_FAVORITE2 = 0x04
STATUS_PATTERN = 0x02

LOG = logging.getLogger("chromacomfort")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def gamma_correct(value: int) -> int:
    value = clamp(value, 0, 255)
    return round(((value / 255.0) ** 2.8) * 255.0)


def build_command(command: int, *, r=0, g=0, b=0, dimmer=0, speed=30) -> bytes:
    payload = bytes([
        0x05, 0x00, 0x40, command,
        clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255),
        clamp(dimmer, 0, 100), clamp(speed, 0, 255),
        0x01, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    ])
    return bytes([START, PAYLOAD_LEN]) + payload


def extract_packets(buffer: bytearray) -> list[bytes]:
    packets = []
    while True:
        try:
            start = buffer.index(START)
        except ValueError:
            buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < 2:
            break
        total = 2 + buffer[1]
        if len(buffer) < total:
            break
        packets.append(bytes(buffer[:total]))
        del buffer[:total]
    return packets


def decode_packet(packet: bytes) -> dict:
    if len(packet) < 2:
        return {"type": "other"}
    length = packet[1]
    payload = packet[2:]
    if length == 4 and len(payload) == 4 and payload[1:3] == bytes([0xA0, 0x40]):
        return {"type": "ack", "payload": payload}
    if length == 17 and len(payload) == 17 and payload[0:3] == bytes([0x05, 0xA0, 0x41]):
        mask = payload[3]
        return {
            "type": "status",
            "mask": mask,
            "fan": bool(mask & STATUS_FAN),
            "white": bool(mask & STATUS_WHITE),
            "wall_rgb": bool(mask & STATUS_WALL_RGB),
            "sweep": bool(mask & STATUS_SWEEP),
            "favorite1": bool(mask & STATUS_FAVORITE1),
            "favorite2": bool(mask & STATUS_FAVORITE2),
            "pattern": bool(mask & STATUS_PATTERN),
            "brightness": payload[5],
            "unknown_9": payload[9],
            "payload": payload,
        }
    return {"type": "other", "payload": payload}


@dataclass
class Settings:
    bluetooth_address: str
    rfcomm_channel: int
    rfcomm_number: int
    rfcomm_device: str
    reconnect_seconds: float
    command_retry_seconds: float
    command_timeout_seconds: float
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
        number = cfg.getint("bluetooth", "rfcomm_number", fallback=0)
        return cls(
            bluetooth_address=cfg.get("bluetooth", "address"),
            rfcomm_channel=cfg.getint("bluetooth", "rfcomm_channel", fallback=7),
            rfcomm_number=number,
            rfcomm_device=cfg.get("bluetooth", "rfcomm_device", fallback=f"/dev/rfcomm{number}"),
            reconnect_seconds=cfg.getfloat("bluetooth", "reconnect_seconds", fallback=5.0),
            command_retry_seconds=cfg.getfloat("bluetooth", "command_retry_seconds", fallback=0.10),
            command_timeout_seconds=cfg.getfloat("bluetooth", "command_timeout_seconds", fallback=5.0),
            mqtt_host=cfg.get("mqtt", "host"),
            mqtt_port=cfg.getint("mqtt", "port", fallback=1883),
            mqtt_username=cfg.get("mqtt", "username", fallback=""),
            mqtt_password=cfg.get("mqtt", "password", fallback=""),
            mqtt_topic=cfg.get("mqtt", "topic_prefix", fallback="chromacomfort/bathroom").rstrip("/"),
            discovery_prefix=cfg.get("mqtt", "discovery_prefix", fallback="homeassistant").rstrip("/"),
            device_name=cfg.get("device", "name", fallback="ChromaComfort"),
            device_id=cfg.get("device", "id", fallback="chromacomfort_bathroom"),
        )


class ChromaComfortBridge:
    def __init__(self, settings: Settings):
        self.s = settings
        self.stop_event = threading.Event()
        self.rfcomm_proc: Optional[subprocess.Popen] = None
        self.serial: Optional[serial.Serial] = None
        self.bt_connected = False
        self.mqtt_connected = False
        self.rx_buffer = bytearray()
        self.tx_packets = 0
        self.rx_packets = 0
        self.ack_count = 0
        self.started = time.monotonic()
        self.last_error = ""
        self.last_command = ""
        self.last_ack = ""
        self.last_rgb = (255, 255, 255)
        self.last_rgb_brightness = 100
        self.command_lock = threading.Lock()
        self.pending_ack = threading.Event()

        try:
            self.mqtt = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"{self.s.device_id}_bridge",
            )
        except AttributeError:
            self.mqtt = mqtt.Client(client_id=f"{self.s.device_id}_bridge")
        if self.s.mqtt_username:
            self.mqtt.username_pw_set(self.s.mqtt_username, self.s.mqtt_password)
        self.mqtt.will_set(self.topic("bridge/availability"), "offline", retain=True)
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_disconnect = self._on_mqtt_disconnect
        self.mqtt.on_message = self._on_mqtt_message

    def topic(self, suffix: str) -> str:
        return f"{self.s.mqtt_topic}/{suffix}"

    def publish(self, suffix: str, payload, *, retain=True) -> None:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, separators=(",", ":"))
        self.mqtt.publish(self.topic(suffix), payload, qos=0, retain=retain)

    def publish_bridge_status(self, status: str) -> None:
        LOG.info("%s", status)
        self.publish("bridge/status", status)

    def publish_error(self, error: str) -> None:
        self.last_error = error
        LOG.error("%s", error)
        self.publish("bridge/last_error", error)

    def publish_diagnostics(self) -> None:
        self.publish("bridge/bluetooth_connected", "ON" if self.bt_connected else "OFF")
        self.publish("bridge/mqtt_connected", "ON" if self.mqtt_connected else "OFF")
        self.publish("bridge/tx_packets", str(self.tx_packets))
        self.publish("bridge/rx_packets", str(self.rx_packets))
        self.publish("bridge/ack_count", str(self.ack_count))
        self.publish("bridge/uptime", str(int(time.monotonic() - self.started)))
        self.publish("bridge/last_command", self.last_command)
        self.publish("bridge/last_ack", self.last_ack)
        self.publish("bridge/last_error", self.last_error)

    def _device(self) -> dict:
        return {
            "identifiers": [self.s.device_id],
            "name": self.s.device_name,
            "manufacturer": "Broan-NuTone",
            "model": "ChromaComfort Sensonic",
        }

    def _discovery(self, component: str, object_id: str, config: dict) -> None:
        config.setdefault("name", object_id.replace("_", " ").title())
        config["unique_id"] = f"{self.s.device_id}_{object_id}"
        config["device"] = self._device()
        topic = f"{self.s.discovery_prefix}/{component}/{self.s.device_id}/{object_id}/config"
        self.mqtt.publish(topic, json.dumps(config), retain=True)

    def publish_discovery(self) -> None:
        self._discovery("fan", "fan", {
            "name": "Fan", "command_topic": self.topic("fan/set"),
            "state_topic": self.topic("fan/state"), "payload_on": "ON", "payload_off": "OFF",
        })
        self._discovery("light", "white_light", {
            "name": "White Light", "command_topic": self.topic("white/set"),
            "state_topic": self.topic("white/state"), "payload_on": "ON", "payload_off": "OFF",
        })
        self._discovery("light", "rgb_light", {
            "name": "RGB Light", "command_topic": self.topic("rgb/set"),
            "state_topic": self.topic("rgb/state"), "payload_on": "ON", "payload_off": "OFF",
            "brightness": True,
            "brightness_command_topic": self.topic("rgb/brightness/set"),
            "brightness_state_topic": self.topic("rgb/brightness/state"),
            "brightness_scale": 255,
            "rgb_command_topic": self.topic("rgb/color/set"),
            "rgb_state_topic": self.topic("rgb/color/state"),
        })
        self._discovery("switch", "wall_rgb_mode", {
            "name": "Wall RGB Mode", "command_topic": self.topic("wall_rgb/set"),
            "state_topic": self.topic("wall_rgb/state"), "payload_on": "ON", "payload_off": "OFF",
        })

        diagnostics = [
            ("sensor", "bridge_status", "Bridge Status", self.topic("bridge/status"), None),
            ("binary_sensor", "bluetooth_connected", "Bluetooth Connected", self.topic("bridge/bluetooth_connected"), "connectivity"),
            ("binary_sensor", "mqtt_connected", "MQTT Connected", self.topic("bridge/mqtt_connected"), "connectivity"),
            ("sensor", "last_error", "Last Error", self.topic("bridge/last_error"), None),
            ("sensor", "last_command", "Last Command", self.topic("bridge/last_command"), None),
            ("sensor", "last_ack", "Last ACK", self.topic("bridge/last_ack"), "timestamp"),
            ("sensor", "tx_packets", "TX Packets", self.topic("bridge/tx_packets"), None),
            ("sensor", "rx_packets", "RX Packets", self.topic("bridge/rx_packets"), None),
            ("sensor", "ack_count", "ACK Count", self.topic("bridge/ack_count"), None),
            ("sensor", "uptime", "Bridge Uptime", self.topic("bridge/uptime"), "duration"),
        ]
        for component, oid, name, state_topic, device_class in diagnostics:
            cfg = {"name": name, "state_topic": state_topic, "entity_category": "diagnostic"}
            if component == "binary_sensor":
                cfg.update({"payload_on": "ON", "payload_off": "OFF"})
            if oid == "uptime":
                cfg.update({"unit_of_measurement": "s", "device_class": "duration"})
            elif device_class:
                cfg["device_class"] = device_class
            self._discovery(component, oid, cfg)

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        self.mqtt_connected = True
        client.subscribe(self.topic("+/set"))
        client.subscribe(self.topic("+/+/set"))
        self.publish("bridge/availability", "online")
        self.publish_discovery()
        self.publish_bridge_status("MQTT connected; initializing Bluetooth")
        self.publish_diagnostics()

    def _on_mqtt_disconnect(self, client, userdata, *args):
        self.mqtt_connected = False
        LOG.warning("MQTT disconnected")

    def _on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        suffix = msg.topic[len(self.s.mqtt_topic) + 1:]
        LOG.info("MQTT command %s = %s", suffix, payload)
        try:
            if suffix == "fan/set":
                self.send_named("fan-on" if payload.upper() == "ON" else "fan-off")
            elif suffix == "white/set":
                self.send_named("white-on" if payload.upper() == "ON" else "white-off")
            elif suffix == "wall_rgb/set":
                self.send_named("wall-rgb-on" if payload.upper() == "ON" else "wall-rgb-off")
            elif suffix == "rgb/set":
                self.set_rgb_power(payload.upper() == "ON")
            elif suffix == "rgb/brightness/set":
                self.set_rgb_brightness(int(payload))
            elif suffix == "rgb/color/set":
                parts = [int(x.strip()) for x in payload.split(",")]
                if len(parts) != 3:
                    raise ValueError("RGB payload must be R,G,B")
                self.set_rgb_color(*parts)
        except Exception as exc:
            self.publish_error(f"Command failed ({suffix}): {exc}")

    def connect_mqtt(self) -> None:
        LOG.info("Connecting MQTT %s:%d", self.s.mqtt_host, self.s.mqtt_port)
        self.mqtt.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)
        self.mqtt.loop_start()

    def _release_rfcomm(self) -> None:
        subprocess.run(
            ["rfcomm", "release", str(self.s.rfcomm_number)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def connect_bluetooth(self) -> None:
        self.disconnect_bluetooth()
        self._release_rfcomm()
        self.publish_bridge_status(
            f"Connecting RFCOMM to {self.s.bluetooth_address} channel {self.s.rfcomm_channel}"
        )
        self.rfcomm_proc = subprocess.Popen(
            ["rfcomm", "connect", str(self.s.rfcomm_number), self.s.bluetooth_address, str(self.s.rfcomm_channel)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.rfcomm_proc.poll() is not None:
                raise ConnectionError(f"rfcomm exited with status {self.rfcomm_proc.returncode}")
            if os.path.exists(self.s.rfcomm_device):
                try:
                    self.serial = serial.Serial(
                        self.s.rfcomm_device, baudrate=115200,
                        timeout=0.20, write_timeout=2,
                    )
                    break
                except serial.SerialException:
                    pass
            time.sleep(0.10)
        else:
            raise TimeoutError(f"Timed out waiting for {self.s.rfcomm_device}")

        self.bt_connected = True
        self.rx_buffer.clear()
        self.publish("bridge/bluetooth_connected", "ON")
        self.publish_bridge_status("Bluetooth RFCOMM connected; waiting for device status")

    def disconnect_bluetooth(self) -> None:
        self.bt_connected = False
        self.publish("bridge/bluetooth_connected", "OFF")
        if self.serial:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        if self.rfcomm_proc and self.rfcomm_proc.poll() is None:
            self.rfcomm_proc.terminate()
            try:
                self.rfcomm_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.rfcomm_proc.kill()
        self.rfcomm_proc = None
        self._release_rfcomm()

    def _send_packet(self, packet: bytes) -> None:
        if not self.serial or not self.bt_connected:
            raise ConnectionError("Bluetooth RFCOMM is not connected")
        self.serial.write(packet)
        self.serial.flush()
        self.tx_packets += 1
        self.publish("bridge/tx_packets", str(self.tx_packets))

    def send_with_ack(self, packet: bytes, command_name: str) -> bool:
        with self.command_lock:
            self.pending_ack.clear()
            self.last_command = command_name
            self.publish("bridge/last_command", command_name)
            self.publish_bridge_status(f"Sending command: {command_name}")
            deadline = time.monotonic() + self.s.command_timeout_seconds
            while time.monotonic() < deadline and not self.stop_event.is_set():
                self._send_packet(packet)
                if self.pending_ack.wait(self.s.command_retry_seconds):
                    self.publish_bridge_status(f"Command acknowledged: {command_name}")
                    return True
            self.publish_error(f"Timeout waiting for ACK: {command_name}")
            return False

    def send_named(self, name: str) -> bool:
        mapping = {
            "fan-on": CMD_FAN_ON, "fan-off": CMD_FAN_OFF,
            "white-on": CMD_WHITE_ON, "white-off": CMD_WHITE_OFF,
            "wall-rgb-on": CMD_WALL_RGB_ON, "wall-rgb-off": CMD_WALL_RGB_OFF,
        }
        return self.send_with_ack(build_command(mapping[name]), name)

    def set_rgb_power(self, enabled: bool) -> bool:
        cmd = CMD_ACTIVATE_FAVORITE1 if enabled else CMD_DEACTIVATE_FAVORITE1
        return self.send_with_ack(
            build_command(cmd, dimmer=self.last_rgb_brightness),
            "rgb-on" if enabled else "rgb-off",
        )

    def set_rgb_brightness(self, brightness_255: int) -> bool:
        brightness_255 = clamp(brightness_255, 0, 255)
        self.last_rgb_brightness = round((brightness_255 / 255.0) * 100)
        ok = self.send_with_ack(
            build_command(CMD_ACTIVATE_FAVORITE1, dimmer=self.last_rgb_brightness),
            f"rgb-brightness-{brightness_255}",
        )
        if ok:
            self.publish("rgb/brightness/state", str(brightness_255))
        return ok

    def set_rgb_color(self, r: int, g: int, b: int) -> bool:
        r, g, b = clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255)
        corrected = (gamma_correct(r), gamma_correct(g), gamma_correct(b))
        ok = self.send_with_ack(
            build_command(CMD_SAVE_FAVORITE1, r=corrected[0], g=corrected[1], b=corrected[2]),
            f"rgb-color-{r},{g},{b}",
        )
        if ok:
            self.last_rgb = (r, g, b)
            self.publish("rgb/color/state", f"{r},{g},{b}")
            ok = self.set_rgb_power(True)
        return ok

    def handle_packet(self, packet: bytes) -> None:
        self.rx_packets += 1
        self.publish("bridge/rx_packets", str(self.rx_packets))
        info = decode_packet(packet)
        if info["type"] == "ack":
            self.ack_count += 1
            self.last_ack = utc_now()
            self.publish("bridge/ack_count", str(self.ack_count))
            self.publish("bridge/last_ack", self.last_ack)
            self.pending_ack.set()
            return
        if info["type"] != "status":
            return
        self.publish("fan/state", "ON" if info["fan"] else "OFF")
        self.publish("white/state", "ON" if info["white"] else "OFF")
        self.publish("wall_rgb/state", "ON" if info["wall_rgb"] else "OFF")
        self.publish("rgb/state", "ON" if info["favorite1"] else "OFF")
        brightness_255 = round(clamp(info["brightness"], 0, 100) * 255 / 100)
        self.publish("rgb/brightness/state", str(brightness_255))
        self.publish("bridge/raw_status", {
            "mask": info["mask"], "brightness": info["brightness"],
            "unknown_9": info["unknown_9"], "sweep": info["sweep"],
            "favorite2": info["favorite2"], "pattern": info["pattern"],
        })
        self.publish_bridge_status("Connected / Ready")

    def bluetooth_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self.bt_connected:
                try:
                    self.connect_bluetooth()
                except Exception as exc:
                    self.disconnect_bluetooth()
                    self.publish_error(f"Bluetooth connection failed: {exc}")
                    self.publish_bridge_status(
                        f"Bluetooth disconnected; retrying in {self.s.reconnect_seconds:g}s"
                    )
                    self.stop_event.wait(self.s.reconnect_seconds)
                    continue
            try:
                if self.rfcomm_proc and self.rfcomm_proc.poll() is not None:
                    raise ConnectionError(f"rfcomm exited with status {self.rfcomm_proc.returncode}")
                assert self.serial is not None
                data = self.serial.read(self.serial.in_waiting or 1)
                if data:
                    self.rx_buffer.extend(data)
                    for packet in extract_packets(self.rx_buffer):
                        self.handle_packet(packet)
                time.sleep(0.005)
            except (serial.SerialException, OSError, ConnectionError) as exc:
                self.publish_error(f"Bluetooth connection lost: {exc}")
                self.disconnect_bluetooth()

    def run(self) -> int:
        self.connect_mqtt()
        worker = threading.Thread(target=self.bluetooth_loop, name="bluetooth", daemon=True)
        worker.start()
        try:
            while not self.stop_event.wait(10):
                if self.mqtt_connected:
                    self.publish_diagnostics()
        finally:
            self.publish_bridge_status("Shutting down")
            self.publish("bridge/availability", "offline")
            self.disconnect_bluetooth()
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        return 0

    def stop(self, *_args) -> None:
        self.stop_event.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="ChromaComfort Linux Bluetooth/MQTT bridge")
    parser.add_argument("--config", default="/etc/chromacomfort/chromacomfort.conf")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.load(args.config)
    except Exception as exc:
        LOG.error("Configuration error: %s", exc)
        return 1
    bridge = ChromaComfortBridge(settings)
    signal.signal(signal.SIGTERM, bridge.stop)
    signal.signal(signal.SIGINT, bridge.stop)
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
