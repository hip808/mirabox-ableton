"""
Measures how often Live itself actually pushes meter updates, independent
of the daemon/device entirely -- so we know whether my rendering pipeline
(already measured at ~2.6ms/frame) or Live's own push rate is the real
latency ceiling before changing anything.
"""
import sys
import time
import threading

sys.path.insert(0, "sdk/Python-SDK/src")

from pythonosc import udp_client, osc_server
from pythonosc.dispatcher import Dispatcher

TRACK_ID = 4  # change to a track with sustained audio on it
LISTEN_SECONDS = 12


def main():
    client = udp_client.SimpleUDPClient("127.0.0.1", 11000)
    arrivals_l = []
    arrivals_r = []

    def handler(address, *args):
        now = time.perf_counter()
        if "output_meter_left" in address:
            arrivals_l.append(now)
        elif "output_meter_right" in address:
            arrivals_r.append(now)

    disp = Dispatcher()
    disp.set_default_handler(handler)
    srv = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 11001), disp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    client.send_message("/live/track/start_listen/output_meter_left", [TRACK_ID])
    client.send_message("/live/track/start_listen/output_meter_right", [TRACK_ID])
    time.sleep(0.3)

    print(f"Listening {LISTEN_SECONDS}s on track {TRACK_ID}. "
          f"Play something with SUSTAINED audio now (not one-shot hits).\n")
    time.sleep(LISTEN_SECONDS)

    client.send_message("/live/track/stop_listen/output_meter_left", [TRACK_ID])
    client.send_message("/live/track/stop_listen/output_meter_right", [TRACK_ID])
    time.sleep(0.2)
    srv.shutdown()

    def report(name, arrivals):
        if len(arrivals) < 2:
            print(f"{name}: only {len(arrivals)} update(s) -- not enough to measure rate.")
            return
        gaps = [(arrivals[i+1] - arrivals[i]) * 1000 for i in range(len(arrivals)-1)]
        avg = sum(gaps) / len(gaps)
        print(f"{name}: {len(arrivals)} updates over {arrivals[-1]-arrivals[0]:.1f}s")
        print(f"   avg gap between updates: {avg:.1f}ms  (~{1000/avg:.1f} updates/sec)")
        print(f"   min gap: {min(gaps):.1f}ms   max gap: {max(gaps):.1f}ms")

    print()
    report("LEFT", arrivals_l)
    print()
    report("RIGHT", arrivals_r)


if __name__ == "__main__":
    main()
