"""
Times the actual cost of a strip-zone write, split into stages, so we know
whether meter latency comes from rendering, the USB transfer, or the
painter loop's own polling interval -- rather than guessing.
"""
import sys
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, ".")
from daemon import render_volume_zone, STRIP_ZONE_LOGICAL  # noqa: E402

N = 15


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
    print(f"Opened {type(device).__name__}\n")

    render_times = []
    save_times = []
    write_times = []
    refresh_times = []
    total_times = []

    for i in range(N):
        level = 0.3 + 0.5 * (i % 3) / 2
        t0 = time.perf_counter()
        img = render_volume_zone(1, "Test Track", volume=0.85, muted=False,
                                 meter_l=level, meter_r=level * 0.7,
                                 db_text="0.0 dB", tap_cc=85)
        t1 = time.perf_counter()
        path = "/tmp/n4_timing_test.jpg"
        img.save(path, quality=90)
        t2 = time.perf_counter()
        device.set_key_image(STRIP_ZONE_LOGICAL[1], path)
        t3 = time.perf_counter()
        device.refresh()
        t4 = time.perf_counter()

        render_times.append(t1 - t0)
        save_times.append(t2 - t1)
        write_times.append(t3 - t2)
        refresh_times.append(t4 - t3)
        total_times.append(t4 - t0)

    def stats(name, vals):
        avg = sum(vals) / len(vals) * 1000
        mx = max(vals) * 1000
        mn = min(vals) * 1000
        print(f"{name:<20} avg={avg:6.1f}ms  min={mn:6.1f}ms  max={mx:6.1f}ms")

    print(f"Over {N} strip-zone writes:\n")
    stats("PIL render", render_times)
    stats("JPEG save", save_times)
    stats("set_key_image (USB)", write_times)
    stats("device.refresh()", refresh_times)
    stats("TOTAL per frame", total_times)

    device.close()


if __name__ == "__main__":
    main()
