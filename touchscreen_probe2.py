"""
Alternative touchscreen test: bypasses set_touchscreen_image entirely and
calls the lower-level "temporary frame" transport function directly (the
one that backs animated GIF/MP4 backgrounds on Pro/XL/M3 devices). This
writes to a different buffer than the "persistent wallpaper" path we've
been using, which may not have the same bug.
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

    img = Image.new("RGB", (800, 480), (0, 255, 0))
    img = img.rotate(180)
    path = "/tmp/n4_probe_green.jpg"
    img.save(path, quality=95)
    with open(path, "rb") as f:
        jpeg_bytes = f.read()
    print(f"Prepared {len(jpeg_bytes)} bytes of JPEG data")

    print("Calling transport.set_background_frame_stream directly ...")
    try:
        res = device.transport.set_background_frame_stream(
            jpeg_bytes, 800, 480, 0, 0, 0x00
        )
        print(f"set_background_frame_stream returned: {res!r}")
    except Exception as e:
        print(f"set_background_frame_stream raised: {e!r}")

    print("Calling refresh() ...")
    device.refresh()

    print("\nNot touching the device again. Watching for 12 seconds -- "
          "check the screen at 1s, 3s, 6s, and 10s for solid green.")
    time.sleep(12)

    device.close()
    print("Done.")


if __name__ == "__main__":
    main()
