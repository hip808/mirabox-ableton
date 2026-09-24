"""Blank every key LCD and strip zone on the N4.

Standalone -- doesn't need Ableton or AbletonOSC running, just the device
itself (and StreamDock.app quit, since only one process can hold the N4
over HID at a time). Useful after daemon.py exits without a clean
shutdown, or on first power-up after moving the device to a new machine,
when the LCDs still show whatever was last pushed to them.

Pushes real blank images to each logical key via set_key_image() -- the
same write path daemon.py uses -- rather than relying on the vendor SDK's
clearAllIcon()/keyAllClear(). That call addresses keys using the SDK's
own (incorrect, per README) model of this unit's layout, so it returns
success without changing anything on screen.
"""

import sys

sys.path.insert(0, "sdk/Python-SDK/src")

from PIL import Image  # noqa: E402
from StreamDock.DeviceManager import DeviceManager  # noqa: E402

KEY_W, KEY_H = 112, 112
STRIP_W, STRIP_H = 176, 112
BLANK = (8, 8, 8)

CLIP_KEYS = range(1, 11)       # the 10 clip/scene keys
STRIP_ZONES = range(11, 15)    # the 4 strip zones


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

    key_img = Image.new("RGB", (KEY_W, KEY_H), BLANK)
    key_path = "/tmp/n4_clear_key.jpg"
    key_img.save(key_path, quality=90)

    strip_img = Image.new("RGB", (STRIP_W, STRIP_H), BLANK)
    strip_path = "/tmp/n4_clear_strip.jpg"
    strip_img.save(strip_path, quality=90)

    for logical_key in CLIP_KEYS:
        device.set_key_image(logical_key, key_path)
    for logical_key in STRIP_ZONES:
        device.set_key_image(logical_key, strip_path)
    device.refresh()

    print(f"Wrote blank images to keys {list(CLIP_KEYS)} and "
          f"strip zones {list(STRIP_ZONES)}.")

    device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
