
#!/usr/bin/env python3
"""
ChromaComfort Bluetooth SPP controller + status monitor.

This is the combined version that:
  * successfully sends commands by retrying until ACK/state change
  * decodes and prints status packets
  * supports continuous status-only monitoring

Requires:
    pip install pyserial

Examples:
    python chromacomfort_control_status.py COM4 status
    python chromacomfort_control_status.py COM4 fan-on
    python chromacomfort_control_status.py COM4 fan-off
    python chromacomfort_control_status.py COM4 light-on
    python chromacomfort_control_status.py COM4 light-off
    python chromacomfort_control_status.py COM4 rgb-on
    python chromacomfort_control_status.py COM4 rgb-off

Optional:
    --retry 0.10
    --timeout 5
    --post-listen 3
"""

import argparse
import sys
import time

import serial


START = 0x3A
PAYLOAD_LEN = 17

COMMANDS = {
    "fan-on": 0x01,
    "fan-off": 0x02,
    "light-on": 0x03,
    "light-off": 0x04,
    "rgb-on": 0x05,
    "rgb-off": 0x06,
}

EXPECTED_STATE = {
    "fan-on":    (0x80, True),
    "fan-off":   (0x80, False),
    "light-on":  (0x40, True),
    "light-off": (0x40, False),
    "rgb-on":    (0x20, True),
    "rgb-off":   (0x20, False),
}


def hexstr(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def build_command(command: int) -> bytes:
    """
    Build the 17-byte ChromaComfort command payload.
    """
    payload = bytes([
        0x05,       # version
        0x00,       # ctrl_cmd_1
        0x40,       # ctrl_cmd_2
        command,    # command
        0x00,       # r
        0x00,       # g
        0x00,       # b
        0x00,       # dimmer
        0x1E,       # speed = 30
        0x01,       # sweep_color_value_1
        0x18,       # sweep_color_value_2 = 24
        0x00,       # duration
        0x00,       # timer_1
        0x00,       # timer_2
        0x00,       # timer_3
        0x00,       # timer_4
        0x00,       # data_end
    ])

    assert len(payload) == PAYLOAD_LEN
    return bytes([START, PAYLOAD_LEN]) + payload


def extract_packets(buffer: bytearray):
    """
    Extract complete packets from a stream.

    Packet format:
        0x3A <payload length> <payload...>
    """
    packets = []

    while True:
        try:
            start_index = buffer.index(START)
        except ValueError:
            buffer.clear()
            break

        if start_index:
            del buffer[:start_index]

        if len(buffer) < 2:
            break

        payload_length = buffer[1]
        total_length = 2 + payload_length

        if len(buffer) < total_length:
            break

        packets.append(bytes(buffer[:total_length]))
        del buffer[:total_length]

    return packets


def decode_packet(packet: bytes):
    """
    Decode known ACK and status packets.
    """
    if len(packet) < 2:
        return {"type": "other"}

    length = packet[1]
    payload = packet[2:]

    # Observed ACK format:
    # 3A 04 05 A0 40 xx
    if (
        length == 4
        and len(payload) == 4
        and payload[1] == 0xA0
        and payload[2] == 0x40
    ):
        return {
            "type": "ack",
            "payload": payload,
        }

    # Observed status format:
    # 3A 11 05 A0 41 ...
    if (
        length == 17
        and len(payload) == 17
        and payload[0] == 0x05
        and payload[1] == 0xA0
        and payload[2] == 0x41
    ):
        mask = payload[3]

        return {
            "type": "status",
            "mask": mask,
            "fan": bool(mask & 0x80),
            "white": bool(mask & 0x40),
            "rgb": bool(mask & 0x20),
            "sweep": bool(mask & 0x10),
            "favorite1": bool(mask & 0x08),
            "favorite2": bool(mask & 0x04),
            "pattern": bool(mask & 0x02),

            # Observed brightness byte:
            "brightness": payload[5],

            # Unknown field that changed from 0x41 to 0x42 during testing.
            # Keeping it visible may help further reverse engineering.
            "unknown_9": payload[9],

            "payload": payload,
        }

    return {
        "type": "other",
        "payload": payload,
    }


def print_packet(packet: bytes, prefix="RX"):
    """
    Print raw packet plus decoded interpretation.
    """
    info = decode_packet(packet)

    print(f"{prefix}: {hexstr(packet)}")

    if info["type"] == "ack":
        print(f"    ACK received: {hexstr(info['payload'])}")

    elif info["type"] == "status":
        print(
            "    STATUS "
            f"fan={'ON' if info['fan'] else 'off'}, "
            f"white={'ON' if info['white'] else 'off'}, "
            f"rgb={'ON' if info['rgb'] else 'off'}, "
            f"sweep={'ON' if info['sweep'] else 'off'}, "
            f"favorite1={'ON' if info['favorite1'] else 'off'}, "
            f"favorite2={'ON' if info['favorite2'] else 'off'}, "
            f"pattern={'ON' if info['pattern'] else 'off'}, "
            f"brightness={info['brightness']}, "
            f"unknown9=0x{info['unknown_9']:02X}"
        )

    return info


def read_packets(ser: serial.Serial, rx_buffer: bytearray):
    """
    Read currently available serial data and return any complete packets.
    """
    waiting = ser.in_waiting
    data = ser.read(waiting if waiting else 1)

    if not data:
        return []

    rx_buffer.extend(data)
    return extract_packets(rx_buffer)


def status_matches(action: str, info: dict) -> bool:
    """
    Return True if a decoded status packet matches the requested action.
    """
    if info.get("type") != "status":
        return False

    bit, desired_on = EXPECTED_STATE[action]
    actual_on = bool(info["mask"] & bit)

    return actual_on == desired_on


def monitor_status(ser: serial.Serial):
    """
    Continuously show status until Ctrl+C.
    """
    print(f"Listening on {ser.port}...")
    print("Press Ctrl+C to stop.\n")

    rx_buffer = bytearray()
    count = 0

    try:
        while True:
            for packet in read_packets(ser, rx_buffer):
                count += 1
                print_packet(packet, f"RX {count:04d}")

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def send_command(
    ser: serial.Serial,
    action: str,
    retry_interval: float,
    timeout: float,
    post_listen: float,
):
    """
    Send a command repeatedly until:
      * ACK is received, OR
      * requested state is observed, OR
      * timeout occurs.

    After ACK/state success, keep listening briefly so the resulting status
    is shown.
    """
    packet = build_command(COMMANDS[action])

    print(f"Port: {ser.port}")
    print(f"Action: {action}")
    print(f"TX packet: {hexstr(packet)}")
    print(
        f"Retry interval: {retry_interval:.3f}s; "
        f"timeout: {timeout:.1f}s; "
        f"post-listen: {post_listen:.1f}s"
    )
    print()

    # Give the Windows RFCOMM connection a moment to settle.
    time.sleep(0.5)

    # Discard old status packets so command results are easier to see.
    ser.reset_input_buffer()

    rx_buffer = bytearray()

    started = time.monotonic()
    next_send = started

    tx_count = 0
    ack_seen = False
    requested_state_seen = False

    while time.monotonic() - started < timeout:
        now = time.monotonic()

        # Retry the command until ACK/state is observed.
        if now >= next_send:
            written = ser.write(packet)
            ser.flush()

            tx_count += 1
            print(f"TX #{tx_count:02d} ({written} bytes)")

            next_send = now + retry_interval

        for rx_packet in read_packets(ser, rx_buffer):
            info = print_packet(rx_packet)

            if info["type"] == "ack":
                ack_seen = True

            if status_matches(action, info):
                requested_state_seen = True
                print(f"    Requested state observed: {action}")

        if ack_seen or requested_state_seen:
            break

        time.sleep(0.005)

    # Important: continue decoding after ACK, because in testing the updated
    # status packet arrived immediately after the ACK.
    if ack_seen or requested_state_seen:
        print()
        print(
            f"Command accepted/observed. "
            f"Listening another {post_listen:g} seconds for status...\n"
        )

        end = time.monotonic() + post_listen

        while time.monotonic() < end:
            for rx_packet in read_packets(ser, rx_buffer):
                info = print_packet(rx_packet, "RX post")

                if status_matches(action, info):
                    requested_state_seen = True

            time.sleep(0.005)

    print()
    print("Result:")
    print(f"  Transmissions: {tx_count}")
    print(f"  ACK seen: {ack_seen}")
    print(f"  Requested state seen: {requested_state_seen}")

    if not ack_seen and not requested_state_seen:
        print("  No ACK or requested state was observed.")
        return 2

    return 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Control and monitor ChromaComfort over a Windows "
            "Bluetooth RFCOMM/SPP COM port."
        )
    )

    parser.add_argument(
        "port",
        help="Bluetooth SPP COM port, e.g. COM4",
    )

    parser.add_argument(
        "action",
        choices=[
            "status",
            "fan-on",
            "fan-off",
            "light-on",
            "light-off",
            "rgb-on",
            "rgb-off",
        ],
        help="Command to send, or 'status' for continuous monitoring.",
    )

    parser.add_argument(
        "--retry",
        type=float,
        default=0.10,
        help="Seconds between command retries (default: 0.10)",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Maximum command retry duration in seconds (default: 5)",
    )

    parser.add_argument(
        "--post-listen",
        type=float,
        default=3.0,
        help="Seconds to monitor status after ACK/state success (default: 3)",
    )

    args = parser.parse_args()

    try:
        with serial.Serial(
            port=args.port,
            baudrate=115200,   # Required by pyserial; irrelevant to RFCOMM
            timeout=0.02,
            write_timeout=2,
        ) as ser:

            if args.action == "status":
                return monitor_status(ser)

            return send_command(
                ser=ser,
                action=args.action,
                retry_interval=args.retry,
                timeout=args.timeout,
                post_listen=args.post_listen,
            )

    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        print(
            f"Make sure no other program (MobaXterm, another Python process, "
            f"etc.) is using {args.port}.",
            file=sys.stderr,
        )
        return 1

    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
