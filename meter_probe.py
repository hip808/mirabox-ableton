"""
Isolated meter test, independent of the daemon and the N4 entirely.

Subscribes to output_meter_level for a track via AbletonOSC's own listener
mechanism (the exact one daemon.py uses) and just prints whatever arrives.
This separates three possible causes of "meter not shown":
  1. AbletonOSC never pushes meter updates at all (a Live/API limitation)
  2. It pushes, but only zeros (nothing is actually playing on that track)
  3. It pushes real values fine, so the bug is in the daemon's rendering
"""
import sys
import time
import threading

sys.path.insert(0, "sdk/Python-SDK/src")

from pythonosc import udp_client, osc_server
from pythonosc.dispatcher import Dispatcher

TRACK_ID = 0  # change to whichever track you'll play audio on
LISTEN_SECONDS = 15


def main():
    client = udp_client.SimpleUDPClient("127.0.0.1", 11000)
    seen = []

    def handler(address, *args):
        seen.append((time.time(), address, args))

    disp = Dispatcher()
    disp.set_default_handler(handler)
    srv = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 11001), disp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print(f"Subscribing to output_meter_level on track {TRACK_ID}...")
    client.send_message("/live/track/start_listen/output_meter_level", [TRACK_ID])
    time.sleep(0.3)

    print(f"Listening {LISTEN_SECONDS}s. Press PLAY in Live now, on a track "
          f"with audio on track index {TRACK_ID} (0 = first track).\n")
    time.sleep(LISTEN_SECONDS)

    client.send_message("/live/track/stop_listen/output_meter_level", [TRACK_ID])
    time.sleep(0.2)
    srv.shutdown()

    meter_msgs = [s for s in seen if s[1] == "/live/track/get/output_meter_level"]
    print(f"\n{len(meter_msgs)} meter update(s) received out of {len(seen)} total messages.")
    if not meter_msgs:
        print("NONE arrived -- AbletonOSC's meter listener is not firing at all.")
        print("This is a Live/API-level issue, not a bug in the daemon's rendering.")
    else:
        nonzero = [m for m in meter_msgs if len(m[2]) >= 2 and m[2][1] > 0.001]
        print(f"{len(nonzero)} of those were nonzero.")
        for t, addr, args in meter_msgs[:20]:
            print(f"  {args}")
        if not nonzero:
            print("\nAll zero -- listener works, but no signal reached the track")
            print("(check you hit Play, and audio is actually routed through it).")
        else:
            print("\nReal values arrived -- the daemon's plumbing should work too.")


if __name__ == "__main__":
    main()
