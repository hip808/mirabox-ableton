"""
Does the strip report WHERE you tapped?

The earlier probe only logged bytes 9 and 10, which showed 0x42 with a
constant 0x00 -- so tap position, if it exists, must live in other bytes.
This dumps the full packet for every tap event, grouped by which quarter of
the strip was tapped, so we can see whether any byte tracks position.

Tap FIRMLY and several times in each zone. Do not touch keys or knobs.
"""
import sys
import time
from collections import Counter

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402

HEADER = bytes.fromhex("41434b00004f4b")
DUMP_BYTES = 24

STEPS = [
    ("hands off (baseline)", 4),
    ("Tap FAR LEFT quarter of the strip", 7),
    ("Tap CENTRE-LEFT quarter", 7),
    ("Tap CENTRE-RIGHT quarter", 7),
    ("Tap FAR RIGHT quarter", 7),
]

current = [None]
log = []


def raw_callback(device, data):
    if len(data) < 11 or bytes(data[0:7]) != HEADER:
        return
    log.append((current[0], bytes(data[:DUMP_BYTES])))


def main():
    time.sleep(0.3)
    manager = DeviceManager()
    devices = manager.enumerate()
    if not devices:
        print("No device found.")
        return

    device = devices[0]
    device.open()
    device.init()
    device.set_raw_read_callback(raw_callback, async_run=False)
    print(f"Opened {type(device).__name__}\n")

    for label, secs in STEPS:
        current[0] = label
        print(f">>> {label}  ({secs}s)", flush=True)
        time.sleep(secs)

    device.close()

    print("\n\n=== FULL PACKETS PER ZONE ===")
    for label, _ in STEPS:
        packets = [p for lbl, p in log if lbl == label]
        print(f"\n-- {label}  ({len(packets)} packets) --")
        for pkt, n in Counter(packets).most_common(6):
            print(f"   x{n:<3} {' '.join(f'{b:02x}' for b in pkt)}")

    print("\n=== WHICH BYTES VARY BETWEEN ZONES? ===")
    zone_packets = {}
    for label, _ in STEPS[1:]:
        pkts = [p for lbl, p in log if lbl == label]
        if pkts:
            zone_packets[label] = pkts

    if len(zone_packets) < 2:
        print("Not enough data captured.")
        return

    for i in range(DUMP_BYTES):
        vals = {lbl: {p[i] for p in pkts} for lbl, pkts in zone_packets.items()}
        allv = set()
        for s in vals.values():
            allv |= s
        if len(allv) > 1:
            print(f"  byte {i:2d} varies: " +
                  "  ".join(f"{lbl.split()[1]}={sorted(hex(v) for v in s)}"
                            for lbl, s in vals.items()))


if __name__ == "__main__":
    main()
