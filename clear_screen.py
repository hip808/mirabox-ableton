"""Blank every key LCD and strip zone on the N4.

Standalone -- doesn't need Ableton or AbletonOSC running, just the device
itself (and StreamDock.app quit, since only one process can hold the N4
over HID at a time). Useful after daemon.py exits without a clean
shutdown, or on first power-up after moving the device to a new machine,
when the LCDs still show whatever was last pushed to them.
"""

import sys

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402


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

    device.clearAllIcon()
    device.refresh()
    print("Cleared all key/strip images.")

    device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
