# Mirabox N4 -> Ableton Live 12 bridge

Personal project. Drives a Mirabox N4 (China-region, base model -- no RGB
LEDs, no real N4 Pro touch hardware) directly over HID, bypassing the
official StreamDock.app entirely, and talks to Ableton Live 12 through
AbletonOSC. See `daemon.py`'s module docstring for the full control layout.

This is a private, personal-use repo -- not published, not supported for
outside users. The hardware protocol (key/knob/swipe/tap codes) was
reverse-engineered empirically on one specific unit; behavior on other N4
units is expected to match but hasn't been verified.

## Requirements

- macOS, Apple Silicon
- Python 3.11+ (native arm64 -- not Rosetta/x86_64; check with
  `python3 -c "import platform; print(platform.machine())"`, must print
  `arm64`)
- Ableton Live 12
- A Mirabox N4

## Setup on a new machine

```bash
git clone <this-repo> mirabox-ableton
cd mirabox-ableton

# Vendored dependencies, not part of this repo (see .gitignore):
git clone --depth 1 https://github.com/MiraboxSpace/StreamDock-Device-SDK.git sdk
git clone --depth 1 https://github.com/ideoforms/AbletonOSC.git AbletonOSC
cp abletonosc-patch/view.py AbletonOSC/abletonosc/view.py
cp abletonosc-patch/track.py AbletonOSC/abletonosc/track.py
cp -r AbletonOSC "$HOME/Music/Ableton/User Library/Remote Scripts/AbletonOSC"

python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

In Live: Preferences -> Link/Tempo/MIDI -> set **AbletonOSC** as a Control
Surface (Input/Output both `None`).

## Running

Start the daemon *before* opening Live (it opens a virtual MIDI port Live
only sees if it exists at launch):

```bash
cd mirabox-ableton && ./venv/bin/python daemon.py
```

Ctrl+C to stop cleanly. `kill` also works now (SIGTERM is handled), but
closing the terminal tab outright can still skip cleanup.

## Why `sdk/` and `AbletonOSC/` aren't in this repo

- `sdk/` is Mirabox's own StreamDock-Device-SDK, cloned whole (143MB,
  including its own git history) -- vendoring it here would bloat the repo
  for no reason when it's one clone command away.
- `AbletonOSC/` is a third-party project (MIT, Daniel Jones) with two files
  modified for this bridge -- adding `/live/view/set/session_highlight`
  (drives Live's control-surface ring) and `/live/track/get/volume_string`
  (Live's own formatted dB readout). Only those two modified files live in
  this repo, under `abletonosc-patch/`, rather than a full duplicate of
  someone else's repo.

## Diagnostic/probe scripts

The various `*_probe.py` files and `capture*.py` are one-off tools used to
reverse-engineer the hardware protocol during development (raw HID capture,
touchscreen behavior tests, MIDI latency checks, meter timing). Not needed
to run the bridge -- kept as documentation and in case the protocol ever
needs re-verifying (new firmware, a second unit, etc).

## Licenses

This project depends on two MIT-licensed projects, not included here:
[StreamDock-Device-SDK](https://github.com/MiraboxSpace/StreamDock-Device-SDK)
(Mirabox) and [AbletonOSC](https://github.com/ideoforms/AbletonOSC) (Daniel
Jones). Not affiliated with or endorsed by Mirabox or Ableton.
