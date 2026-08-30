#!/usr/bin/env python3
"""Generate small built-in ChromaComfort alert WAV files with no external assets."""

from __future__ import annotations

import argparse
import math
import os
import struct
import wave

RATE = 48000
AMPLITUDE = 0.28


def envelope(position: int, total: int, attack_ms: float = 10.0, release_ms: float = 120.0) -> float:
    attack = max(1, int(RATE * attack_ms / 1000.0))
    release = max(1, int(RATE * release_ms / 1000.0))
    gain = 1.0
    if position < attack:
        gain *= position / attack
    remaining = total - position - 1
    if remaining < release:
        gain *= max(0.0, remaining / release)
    return gain


def tone(freq: float, seconds: float, amplitude: float = AMPLITUDE) -> list[float]:
    count = int(RATE * seconds)
    return [
        amplitude * envelope(i, count) * math.sin(2.0 * math.pi * freq * i / RATE)
        for i in range(count)
    ]


def chord(freqs: tuple[float, ...], seconds: float, amplitude: float = AMPLITUDE) -> list[float]:
    count = int(RATE * seconds)
    divisor = max(1, len(freqs))
    return [
        amplitude * envelope(i, count)
        * sum(math.sin(2.0 * math.pi * f * i / RATE) for f in freqs)
        / divisor
        for i in range(count)
    ]


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def concat(*parts: list[float]) -> list[float]:
    result: list[float] = []
    for part in parts:
        result.extend(part)
    return result


def write_wav(path: str, samples: list[float]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        frames = bytearray()
        for sample in samples:
            value = max(-1.0, min(1.0, sample))
            packed = struct.pack("<h", int(value * 32767))
            frames.extend(packed)
            frames.extend(packed)
        wav.writeframes(frames)


def build_sounds() -> dict[str, list[float]]:
    return {
        "doorbell": concat(
            chord((659.25, 783.99), 0.42),
            silence(0.12),
            chord((523.25, 659.25), 0.62),
        ),
        "complete": concat(
            tone(523.25, 0.16), silence(0.04),
            tone(659.25, 0.16), silence(0.04),
            tone(783.99, 0.34),
        ),
        "alert": concat(
            tone(880.00, 0.14), silence(0.08),
            tone(880.00, 0.14), silence(0.08),
            tone(880.00, 0.22),
        ),
        "notification": concat(
            chord((659.25, 987.77), 0.20),
            silence(0.05),
            chord((783.99, 1174.66), 0.30),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate built-in ChromaComfort WAV alerts")
    parser.add_argument("--output-dir", default="/opt/chromacomfort/sounds")
    args = parser.parse_args()

    for name, samples in build_sounds().items():
        path = os.path.join(args.output_dir, f"{name}.wav")
        write_wav(path, samples)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
