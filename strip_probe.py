"""
Verify the horizontal strip is addressable as logical keys 11-14 (the
SDK's "secondary screen", 176x112 per zone) rather than via the
whole-face background wallpaper we were wrongly using.

Writes four distinct colours + numbers, one per zone, left to right.
"""
import sys
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

COLORS = [(200, 40, 40), (40, 180, 70), (50, 90, 220), (220, 190, 40)]


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

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
    except Exception:
        font = ImageFont.load_default()

    for zone in range(1, 5):
        logical_key = 10 + zone
        img = Image.new("RGB", (176, 112), COLORS[zone - 1])
        draw = ImageDraw.Draw(img)
        text = str(zone)
        w = draw.textlength(text, font=font)
        draw.text(((176 - w) / 2, 16), text, font=font, fill=(255, 255, 255))
        path = f"/tmp/n4_strip_{zone}.jpg"
        img.save(path, quality=92)
        res = device.set_key_image(logical_key, path)
        print(f"zone {zone} -> logical key {logical_key}: returned {res!r}")

    device.refresh()
    print("\nLook at the horizontal strip. Expect, left to right:")
    print("  RED 1 | GREEN 2 | BLUE 3 | YELLOW 4")
    time.sleep(8)

    device.close()
    print("Done.")


if __name__ == "__main__":
    main()
