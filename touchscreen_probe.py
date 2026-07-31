"""
Isolated touchscreen test: bypasses the daemon entirely, sends one trivial
solid-color image, and prints the actual return value from the SDK call
(which the daemon never checked) so we can see if it's silently failing.
"""
import sys
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402
from PIL import Image  # noqa: E402


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
    print(f"Opened {type(device).__name__}, firmware {device.firmware_version}")

    img = Image.new("RGB", (800, 480), (255, 0, 0))
    path = "/tmp/n4_probe_red.jpg"
    img.save(path, quality=95)
    print(f"Saved test image to {path}, size {img.size}")

    print("Calling set_touchscreen_image ...")
    res = device.set_touchscreen_image(path)
    print(f"set_touchscreen_image returned: {res!r}")

    print("Calling refresh() ...")
    res2 = device.refresh()
    print(f"refresh() returned: {res2!r}")

    print("\nNot touching the device again. Watching for 12 seconds -- "
          "check the screen at 1s, 3s, 6s, and 10s to see if it ever "
          "shows red, even briefly.")
    time.sleep(12)

    device.close()
    print("Done.")


if __name__ == "__main__":
    main()
