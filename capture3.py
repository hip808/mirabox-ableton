"""
Full mapping capture: prompts for every key and knob in a known physical
order, tags packets by step, so we get an exact hw-code -> physical
position table. No writes to the device.
"""
import sys
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402

STEPS = [
    ("Press KEY at top row, position 1 (leftmost)", 3),
    ("Press KEY at top row, position 2", 3),
    ("Press KEY at top row, position 3", 3),
    ("Press KEY at top row, position 4", 3),
    ("Press KEY at top row, position 5 (rightmost)", 3),
    ("Press KEY at bottom row, position 1 (leftmost)", 3),
    ("Press KEY at bottom row, position 2", 3),
    ("Press KEY at bottom row, position 3", 3),
    ("Press KEY at bottom row, position 4", 3),
    ("Press KEY at bottom row, position 5 (rightmost)", 3),
    ("Turn KNOB 1 (leftmost) slightly clockwise", 3),
    ("Push KNOB 1 (leftmost)", 3),
    ("Turn KNOB 2 slightly clockwise", 3),
    ("Push KNOB 2", 3),
    ("Turn KNOB 3 slightly clockwise", 3),
    ("Push KNOB 3", 3),
    ("Turn KNOB 4 (rightmost) slightly clockwise", 3),
    ("Push KNOB 4 (rightmost)", 3),
]

current_step = [None]
log = []


def raw_callback(device, data):
    hexstr = " ".join(f"{b:02x}" for b in data[9:11])
    log.append((current_step[0], hexstr))


def main():
    time.sleep(0.3)
    manager = DeviceManager()
    devices = manager.enumerate()
    if not devices:
        print("No Stream Dock device found.")
        return

    device = devices[0]
    device.open()
    device.init()
    device.set_raw_read_callback(raw_callback, async_run=False)

    print("Starting in 3...", flush=True)
    time.sleep(1)
    print("2...", flush=True)
    time.sleep(1)
    print("1...", flush=True)
    time.sleep(1)

    for label, duration in STEPS:
        current_step[0] = label
        print(f"\n>>> NOW: {label}  ({duration}s)", flush=True)
        time.sleep(duration)

    device.close()

    print("\n\n=== MAPPING RESULTS ===")
    for label, _ in STEPS:
        rows = [r[1] for r in log if r[0] == label]
        seen = {}
        for hexstr in rows:
            seen[hexstr] = seen.get(hexstr, 0) + 1
        summary = ", ".join(f"{h}(x{c})" for h, c in sorted(seen.items(), key=lambda x: -x[1]))
        print(f"{label:55s} -> {summary if summary else '(nothing captured)'}")


if __name__ == "__main__":
    main()
