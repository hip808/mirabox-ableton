# Mirabox N4 → Ableton Live 12 bridge

A direct hardware bridge between a China-region Mirabox N4 and Ableton Live
12. It bypasses Mirabox's official StreamDock.app entirely — including the
region lock that forces the stock app into Chinese — by talking to the N4's
USB HID interface directly. Every key, knob, swipe, and tap gesture was
reverse-engineered by capturing raw device traffic, since the vendor's own
SDK doesn't correctly describe this unit's protocol. The result turns the
N4 into a from-scratch Ableton controller: live clip/scene launching with
real Session View colors, per-track volume with a genuine dB meter, and
two-way visual sync back into Live itself.

Personal project, privately hosted. Not affiliated with or endorsed by
Mirabox or Ableton.

## Purpose

Mirabox sells the N4 in two channels: an international "Stream Dock" SKU
and a China-region SKU. The China unit's bundled app refuses to run in any
language but Chinese and blocks customization. Rather than accept that,
this project talks to the hardware over raw USB HID — the same low-level
interface the official app uses — and replaces it outright with a
purpose-built Ableton controller. Nothing here depends on the official
app; it can be fully quit and never opened again.

## Requirements

- macOS, Apple Silicon (arm64 — not Rosetta/x86_64; the SDK's transport
  library is arm64-only)
- Python 3.11+
- Ableton Live 12
- A Mirabox N4 (base/China-region model — no RGB LEDs, no touchscreen
  digitizer; see Known Issues)
- Python packages: `pillow`, `python-osc`, `python-rtmidi` (`requirements.txt`)
- [AbletonOSC](https://github.com/ideoforms/AbletonOSC) installed as a Live
  Control Surface, with two files patched (included in this repo — see
  `abletonosc-patch/`)

## Full functionality

**Clip and scene launching**
- Keys 1–4 and 6–9 fire clips — 4 tracks wide, top row fires the current
  scene, bottom row fires the next scene down. Each key is rendered live
  with that clip's actual color and name straight from Session View.
- Keys 5 and 10 fire whole scenes (the Main/Master track's column in
  Session View), colored and named from Live, scrolling in lockstep with
  the clip keys.
- The visible 4-track window follows whatever track is selected in Live —
  select a track with the mouse or keyboard and the hardware's layout
  shifts to match, no manual paging needed.

**Navigation**
- Knob 3 moves Live's track selection left/right; knob 4 moves its scene
  selection up/down. Both drive Live's real selection highlight directly —
  there's no API to draw a custom indicator, so this moves the actual one.
- Swiping the strip left-to-right or right-to-left jumps the track window
  by 4 tracks in one gesture. (The N4's strip does report touch position,
  despite the vendor SDK's own device class having no touch-handling code
  at all for this model — this was found by capturing raw HID traffic
  with a silent idle baseline to confirm the codes were genuine input.)

**Mixing**
- Knobs 1 and 2 control volume for the two leftmost visible tracks. One
  full mechanical turn spans 0–100%, and turning through unity gain
  always lands exactly on 0dB rather than skipping past it.
- Each knob push toggles mute for its track.
- The strip shows a live two-bar stereo meter (L and R, matching how
  Live's own mixer always shows both regardless of source channel count)
  per track — log-scaled in dB, not a linear fill of raw amplitude, and
  colored with Ableton's own gradient stops (extracted directly from
  Live's shipped theme files, not approximated). A small triangle marks
  the fader's own position on the same dB scale, separate from the meter.
- The strip also shows channel number, track name, and Live's own
  formatted dB readout for the fader.

**MIDI**
- Knob 3 and 4 pushes, and all four zones of a strip tap, each send their
  own MIDI CC number over a virtual port (`Mirabox N4`), free to map to
  anything in Live — transport, Record, device on/off, whatever's useful.
  Configurable per-CC as either a momentary trigger or a latching toggle.

**Two-way feedback**
- The hardware's current clip window is mirrored back into Live as a
  control-surface ring — the colored box Push/APC-style controllers draw
  around their active clip window in Session View. This required adding
  an endpoint to AbletonOSC, which doesn't expose it by default (found by
  decompiling Ableton's own bundled Control Surface base class to locate
  `_set_session_highlight`).
- Moving a fader in Live, muting a track, or selecting a different
  track/scene from the mouse all reflect back onto the hardware live.

**Other**
- Track and scene counts are re-read from Live on every refresh, so
  adding tracks/scenes or loading a different set never needs a restart.
- Shuts down cleanly on both Ctrl+C and `kill` (SIGTERM), unsubscribing
  from Live so listeners don't pile up across restarts.

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

In Live: Preferences → Link/Tempo/MIDI → set **AbletonOSC** as a Control
Surface (Input/Output both `None`).

## Running

Quit StreamDock.app first — only one process can hold the N4 over HID at
a time. Start the daemon **before** opening Live (it opens a virtual MIDI
port and patches AbletonOSC's behavior; Live only picks both up if they
exist when it launches):

```bash
cd mirabox-ableton && ./venv/bin/python daemon.py
```

Ctrl+C to stop cleanly. `kill` also works now (SIGTERM is handled), but
closing the terminal tab outright can still skip cleanup.

## Known issues

- **Verified on one physical unit.** The exact key/knob/swipe/tap byte
  codes were measured empirically on one N4. Almost certainly identical
  on any N4 with the same firmware (it's the same manufactured product),
  but not confirmed on a second unit.
- **macOS Apple Silicon only.** No Windows/Linux support, despite
  Mirabox's own SDK supporting both — untested, not ported.
- **Exclusive hardware access.** StreamDock.app and this daemon can't run
  at the same time; whichever opens the device first wins.
- **Startup order matters.** The daemon must be running before Live
  launches, or Live won't see the virtual MIDI port or the patched
  AbletonOSC behavior until it's restarted.
- **The whole-face background image function doesn't work reliably** and
  isn't used. `set_touchscreen_image` turned out to be a single wallpaper
  sliced across every key LCD rather than a real touchscreen — painting it
  wiped the key images. Abandoned in favor of the 4-zone strip display,
  which works correctly.
- **Meters are post-fader**, matching Live's own default meter behavior —
  not switchable to pre-fader/input level.
- **CC momentary/latch behavior is a hardcoded set** (`CC_LATCH` in
  `daemon.py`) — changing it means editing the source and restarting, not
  a runtime setting.
- **No status UI.** Everything is `print()` output in Terminal. Fine for
  one technical user, not friendly for anyone else.
- **Not packaged as an app.** Must be run from Terminal in this exact
  directory; no menu bar icon, no auto-start on login.
- **No hot-plug handling.** Unplugging and replugging the N4 mid-session
  isn't handled — the daemon needs a restart to reconnect.

## Future upgrades

- Package as a macOS LaunchAgent so it starts automatically, in the right
  order, without touching Terminal.
- Full packaged `.app` with a menu bar status icon, for sharing with
  non-technical users.
- Verify hardware codes against a second N4 unit; extend to other Mirabox
  models (real N4 Pro with touch/RGB, XL, M3).
- Windows/Linux port.
- Runtime-configurable CC assignments and momentary/latch behavior,
  instead of source constants.
- Hot-plug detection and auto-reconnect.
- Volume control for more than the leftmost 2 tracks at once (currently
  limited to 2 since knobs 3/4 are dedicated to navigation).

## Why `sdk/` and `AbletonOSC/` aren't in this repo

- `sdk/` is Mirabox's own StreamDock-Device-SDK, cloned whole (143MB,
  including its own git history) — vendoring it here would bloat the repo
  for no reason when it's one clone command away.
- `AbletonOSC/` is a third-party project (MIT, Daniel Jones) with two files
  modified for this bridge — adding `/live/view/set/session_highlight`
  (drives Live's control-surface ring) and `/live/track/get/volume_string`
  (Live's own formatted dB readout). Only those two modified files live in
  this repo, under `abletonosc-patch/`, rather than a full duplicate of
  someone else's repo.

## Diagnostic/probe scripts

The various `*_probe.py` files and `capture*.py` are one-off tools used to
reverse-engineer the hardware protocol during development (raw HID capture,
touchscreen behavior tests, MIDI latency checks, meter timing). Not needed
to run the bridge — kept as documentation and in case the protocol ever
needs re-verifying (new firmware, a second unit, etc).

## Licenses

This project depends on two MIT-licensed projects, not included here:
[StreamDock-Device-SDK](https://github.com/MiraboxSpace/StreamDock-Device-SDK)
(Mirabox) and [AbletonOSC](https://github.com/ideoforms/AbletonOSC) (Daniel
Jones). Not affiliated with or endorsed by Mirabox or Ableton.
