"""One-shot discovery: what does the SDK see for whatever's plugged in?

Standalone, read-only (aside from init/close housekeeping) -- confirms
device class, firmware, key count, and feature flags before any N4 Pro
support gets written into daemon.py.
"""

import sys

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402
from StreamDock.Devices.StreamDockN4Pro import StreamDockN4Pro  # noqa: E402


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

    print(f"Class:        {type(device).__name__}")
    print(f"Is N4 Pro:    {isinstance(device, StreamDockN4Pro)}")
    print(f"Firmware:     {device.firmware_version}")
    print(f"KEY_COUNT:    {getattr(device, 'KEY_COUNT', '?')}")

    fo = getattr(device, "feature_option", None)
    if fo:
        print("Feature option:")
        for attr in dir(fo):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(fo, attr)
            except Exception:
                continue
            if callable(val):
                continue
            print(f"  {attr} = {val}")

    image_map = getattr(device, "_IMAGE_KEY_MAP", None)
    if image_map:
        print("Logical -> hardware key map:")
        for k, v in image_map.items():
            print(f"  {k} -> {v}")

    device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
