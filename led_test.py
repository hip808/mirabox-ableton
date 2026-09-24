"""Cycle known pure colors on the N4 Pro's knob LEDs, one at a time, with
a pause between each -- so a human watching the device can report back
what actually appears, to tell a channel-order bug (e.g. GRB vs RGB, which
would show the wrong hue entirely) apart from a diffuser/gamma effect
(which would show the right hue, just muted).

Standalone -- doesn't need Ableton, just the device (StreamDock.app quit).
"""

import sys
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402

STEPS = [
    ("RED",   (255, 0, 0)),
    ("GREEN", (0, 255, 0)),
    ("BLUE",  (0, 0, 255)),
    ("WHITE", (255, 255, 255)),
    ("OFF",   (0, 0, 0)),
]
HOLD_SECONDS = 12


def main():
    manager = DeviceManager()
    devices = manager.enumerate()
    if not devices:
        print("No Stream Dock device found. Is it plugged in, and is "
              "StreamDock.app quit?")
        return 1

    device = devices[0]
    device.open()
    device.init()
    print(f"Connected to {type(device).__name__}, firmware "
          f"{device.firmware_version}")

    device.set_led_brightness(100)

    print("Starting in 6s -- look at the device now.")
    time.sleep(6)

    for name, rgb in STEPS:
        print(f"--> sending {name} {rgb} to all 4 knob LEDs, "
              f"holding {HOLD_SECONDS}s")
        device.set_single_led_color([rgb, rgb, rgb, rgb])
        time.sleep(HOLD_SECONDS)

    device.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
