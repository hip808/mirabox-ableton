"""
Independent MIDI monitor for the bridge's virtual port.

Run this in a SECOND terminal while daemon.py is running, then push knob 3
and knob 4. It listens to the 'Mirabox N4' port from the outside, so it
proves whether CC messages actually leave the process -- separating "not
sending" from "Ableton not receiving".
"""
import sys
import time

import rtmidi

LISTEN_SECONDS = 25
PORT_MATCH = "Mirabox N4"


WAIT_SECONDS = 60


def find_port(midi_in):
    for i, name in enumerate(midi_in.get_ports()):
        if PORT_MATCH in name:
            return i, name
    return None, None


def main():
    midi_in = rtmidi.MidiIn()

    target, port_name = find_port(midi_in)
    if target is None:
        print(f"Waiting up to {WAIT_SECONDS}s for '{PORT_MATCH}' to appear.")
        print("Start daemon.py in another terminal now.\n")
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(1)
            # get_ports() caches; a fresh MidiIn re-scans CoreMIDI.
            midi_in = rtmidi.MidiIn()
            target, port_name = find_port(midi_in)
            if target is not None:
                break

    if target is None:
        print(f"'{PORT_MATCH}' never appeared. Is daemon.py running?")
        print("Visible sources:")
        for name in rtmidi.MidiIn().get_ports():
            print("  -", name)
        sys.exit(1)

    midi_in.open_port(target)
    ports = [port_name]
    target = 0
    print(f"Listening on '{ports[target]}' for {LISTEN_SECONDS}s.")
    print("Push knob 3 and knob 4 now (several times each).\n")

    seen = 0
    deadline = time.time() + LISTEN_SECONDS
    while time.time() < deadline:
        msg = midi_in.get_message()
        if msg:
            data, _ = msg
            if len(data) >= 3:
                status, cc, value = data[0], data[1], data[2]
                channel = (status & 0x0F) + 1
                print(f"  CC {cc:<4} value {value:<4} channel {channel}")
            else:
                print("  raw:", data)
            seen += 1
        time.sleep(0.005)

    midi_in.close_port()
    print(f"\n{seen} message(s) received.")
    if seen == 0:
        print("Nothing arrived -- the CC is not leaving daemon.py.")
    else:
        print("CCs are leaving correctly, so any problem is on the Live side")
        print("(port not enabled for Remote in Preferences, or not mapped).")


if __name__ == "__main__":
    main()
