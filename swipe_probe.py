"""
Swipe confirmation, second pass.

Round one proved the strip reports gestures (0x38 / 0x39 / 0x42) but logged
every packet without the ACK..OK input-event header the daemon filters on,
so idle status packets showed up as phantom "hands off" hits. This applies
the same header filter the daemon uses, to confirm the gesture codes are
genuine input and that idle really is silent.

Do NOT touch the knobs or keys during this test.
"""
import sys
import time
from collections import Counter

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402

HEADER = bytes.fromhex("41434b00004f4b")  # "ACK\x00\x00OK"

STEPS = [
    ("BASELINE - hands completely off", 5),
    ("Swipe LEFT to RIGHT, repeatedly", 8),
    ("hands off", 4),
    ("Swipe RIGHT to LEFT, repeatedly", 8),
    ("hands off", 4),
]

current = [None]
log = []


def raw_callback(device, data):
    # Same gate the daemon applies: only real input-event packets.
    if len(data) < 11 or bytes(data[0:7]) != HEADER:
        return
    log.append((current[0], data[9], data[10]))


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
    print("Do NOT touch the knobs or keys.\n")

    for label, secs in STEPS:
        current[0] = label
        print(f">>> {label}  ({secs}s)", flush=True)
        time.sleep(secs)

    device.close()

    print("\n\n=== RESULTS (header-filtered) ===")
    for label, _ in STEPS:
        counts = Counter((c, v) for lbl, c, v in log if lbl == label)
        print(f"\n-- {label} --")
        if not counts:
            print("   (silent)")
            continue
        for (code, val), n in counts.most_common():
            print(f"   0x{code:02x} val=0x{val:02x}  x{n}")


if __name__ == "__main__":
    main()
