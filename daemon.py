"""
Mirabox N4 -> Ableton Live 12 bridge.

Reads raw HID packets from the N4 directly (bypassing StreamDock.app and its
region lock), decodes keys/knobs using hardware codes measured empirically
on this exact unit, and drives Ableton Live over AbletonOSC.

Display model (learned the hard way -- see conversation history):
  The N4 has 14 addressable displays, not 10 + a touchscreen.
    logical keys 1-10  -> the ten 112x112 key LCDs
    logical keys 11-14 -> four 176x112 zones making up the horizontal strip,
                          one sitting directly above each knob
  `set_touchscreen_image` is NOT the strip -- it is a whole-face background
  wallpaper that gets sliced across every LCD, so painting it wipes the key
  images. We never use it.

Layout (horizontal position follows Live's own track selection):
  Keys 1-4 / 6-9 -> fire clips. Top row = scene cursor's row, bottom row =
                  the next scene row. 4 tracks wide, starting at the track
                  selected in Live. Each key shows that clip's own colour and
                  name from Session View, dimmed to 28% brightness when
                  loaded but not playing -- full brightness means actually
                  playing (or queued to start), near-black means empty. This
                  updates live: firing a clip lights it immediately, and a
                  Live-side listener confirms/corrects and reflects clips
                  started or stopped from Live's own UI or ending on their own.
  Keys 5 / 10  -> scene launch for those same two rows -- the Main (Master)
                  track's column in Session view. They scroll with the clip
                  keys, and show each scene's name and colour.
  Knob 1, 2    -> volume for the leftmost 2 of those tracks
                  (one full turn = 0-100%); push = toggle mute
  Knob 3       -> move Live's selection left/right through tracks
  Knob 4       -> move Live's selection up/down through scenes
                  (these drive Live's own native highlight around the
                  selected clip slot -- the Live API has no way to draw a
                  custom "red box", so we move the real one)
  Knob 3 push  -> MIDI CC 85 on a virtual port, free to MIDI-map in Live
  Knob 4 push  -> MIDI CC 86, likewise
  Swipe strip  -> left-to-right jumps forward 4 tracks, right-to-left back 4.
                  The panel does report gestures even though the vendor SDK's
                  base-N4 class has no touch handling; codes measured directly.
  Tap strip    -> four zones left to right -> MIDI CC 87/88/89/90, momentary.
                  The strip reports tap position in byte 9 (0x40..0x43).

Two-way feedback: the hardware's 4x2 clip window is mirrored back into Live
as a control surface ring (the box Push/APC controllers draw on Session
view), via a session_highlight endpoint added to the installed AbletonOSC.
  Strip zone 1,2 -> channel number, track name, volume, mute for knobs 1-2
  Strip zone 3,4 -> current track / scene cursor position for knobs 3-4

Track and scene counts are re-read from Live on every refresh, so adding
tracks/scenes or loading a different set is picked up without a restart.
"""
import math
import signal
import sys
import threading
import time

sys.path.insert(0, "sdk/Python-SDK/src")

from StreamDock.DeviceManager import DeviceManager  # noqa: E402
from pythonosc import udp_client, osc_server  # noqa: E402
from pythonosc.dispatcher import Dispatcher  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
import rtmidi  # noqa: E402

HEADER = bytes.fromhex("41434b00004f4b")  # "ACK\x00\x00OK"

# Raw input hardware codes, measured directly on this unit (the SDK's own
# input table does not match this firmware -- see conversation history).
KEY_CODES = {
    0x01: "top1", 0x02: "top2", 0x03: "top3", 0x04: "top4", 0x05: "top5",
    0x06: "bot1", 0x07: "bot2", 0x08: "bot3", 0x09: "bot4", 0x0a: "bot5",
}
KNOB_ROTATE_CODES = {
    0xa0: (1, -1), 0xa1: (1, +1),
    0x50: (2, -1), 0x51: (2, +1),
    0x90: (3, -1), 0x91: (3, +1),
    0x70: (4, -1), 0x71: (4, +1),
}
KNOB_PUSH_CODES = {0x37: 1, 0x35: 2, 0x33: 3, 0x36: 4}

# Touchscreen swipes. The strip DOES report gestures, contrary to the
# vendor SDK's base-N4 class (which has no touch handling at all) -- these
# codes were measured directly, with a silent idle baseline confirming they
# are genuine input. One swipe emits ~2 events, hence the debounce.
SWIPE_CODES = {0x38: +1, 0x39: -1}  # 0x38 = left-to-right, 0x39 = right-to-left
SWIPE_TRACKS = 4                    # tracks moved per swipe
SWIPE_DEBOUNCE = 0.30               # seconds; collapses one swipe to one action
# Tap zones. Byte 9 encodes WHERE the strip was tapped -- 0x40..0x43 map to
# the four quarters left to right, lining up with the four knobs. Measured
# directly; the vendor SDK exposes none of this. CCs stay inside the MIDI
# spec's undefined 85-90 block alongside the knob-push CCs.
TAP_ZONE_CC = {0x40: 85, 0x41: 86, 0x42: 87, 0x43: 88}
TAP_DEBOUNCE = 0.20  # per zone, so quick taps across different zones all land

# Live's control surface ring -- the box Session view draws around the
# hardware's current clip window. Height 2 = the two key rows.
RING_HEIGHT = 2

# Output (image-setting) logical key numbers -- a separate namespace from
# the raw input codes above.
ROW1_KEY_TO_LOGICAL = {"top1": 1, "top2": 2, "top3": 3, "top4": 4}
ROW2_KEY_TO_LOGICAL = {"bot1": 6, "bot2": 7, "bot3": 8, "bot4": 9}
ROW1_KEY_TO_COLUMN = {"top1": 0, "top2": 1, "top3": 2, "top4": 3}
ROW2_KEY_TO_COLUMN = {"bot1": 0, "bot2": 1, "bot3": 2, "bot4": 3}

# Keys 5 and 10 sit in the Main (Master) track's column, which in Session
# view holds the scene launch buttons -- so they fire whole scenes, for the
# same two rows the clip keys are showing.
SCENE_KEY_LOGICAL = {"top5": 5, "bot5": 10}
SCENE_DEFAULT_COLOR = (55, 55, 62)

# Reverse lookups: column -> logical key, so a single clip's key image can be
# redrawn on its own (e.g. right when it starts/stops playing) without a
# full refresh of all 10 keys.
COL_TO_ROW1_LOGICAL = {ROW1_KEY_TO_COLUMN[k]: v for k, v in ROW1_KEY_TO_LOGICAL.items()}
COL_TO_ROW2_LOGICAL = {ROW2_KEY_TO_COLUMN[k]: v for k, v in ROW2_KEY_TO_LOGICAL.items()}
ROW1_LOGICAL_TO_COL = {v: k for k, v in COL_TO_ROW1_LOGICAL.items()}
ROW2_LOGICAL_TO_COL = {v: k for k, v in COL_TO_ROW2_LOGICAL.items()}

# Loaded-but-stopped clips are dimmed to this fraction of their real color,
# so at a glance: near-black = empty, dim = loaded, full brightness =
# actually playing (or queued to start) -- the same convention Push and
# other session-view controllers use.
CLIP_DIM_FACTOR = 0.28

# Triggered (queued to start, not yet playing) blinks between full and dim
# rather than being lumped in with "playing" -- otherwise there's no way to
# tell "about to play" apart from "already playing" at a glance.
BLINK_HZ = 2.5


def dim_color(rgb, factor=CLIP_DIM_FACTOR):
    return tuple(max(0, int(round(c * factor))) for c in rgb)


def clip_key_color(rgb, playing, triggered, blink_on, empty_color=None):
    """The single place that turns (color, playing, triggered) into what
    actually gets drawn: full brightness = playing, blinking = triggered
    (queued, not yet playing), dim = loaded but idle, untouched = empty."""
    if empty_color is not None and rgb == empty_color:
        return rgb
    if playing:
        return rgb
    if triggered:
        return rgb if blink_on else dim_color(rgb)
    return dim_color(rgb)

STRIP_ZONE_LOGICAL = {1: 11, 2: 12, 3: 13, 4: 14}
STRIP_W, STRIP_H = 176, 112
KEY_W, KEY_H = 112, 112

KEY_COLUMNS = 4
VOLUME_KNOBS = (1, 2)
EMPTY_CLIP_COLOR = (25, 25, 25)

# Knob LEDs -- N4 Pro only (base N4 has none; DeviceIO.write_leds() is a
# silent no-op there since the SDK gates it on feature_option.hasRGBLed).
# Knobs 1/2 (mixing) mirror that column's own clip color, dimmed on mute
# with the same factor already used for non-playing clips. Knobs 3/4
# (track/scene nav) aren't tied to a single color, so they stay a fixed
# idle tone that reads as "navigation," not "channel."
KNOB_LED_BRIGHTNESS = 70
KNOB_LED_IDLE = (30, 34, 46)
KNOB_LED_MUTE_DIM = 0.10

# Measured ~16 rotation ticks per full mechanical turn of a knob, so one
# full turn spans 0-100% volume.
TICKS_PER_FULL_TURN = 16
VOLUME_STEP = 1.0 / TICKS_PER_FULL_TURN

# Live's fader is non-linear and unity sits at exactly 0.85 (verified against
# this set: value 0.8500 reads back as "0.0 dB"). Stepping by a fixed amount
# would skip straight past it, so a turn that crosses unity lands on it.
ZERO_DB_VALUE = 0.85

# output_meter_level/left/right are raw linear amplitude (1.0 = 0dBFS), not
# pre-scaled to match Live's on-screen meter -- confirmed against sustained
# playback where 20*log10(value) landed on ordinary, sane dB readings for
# normal program material (e.g. ~0.80 -> -1.9dB, ~0.46 -> -6.7dB). Log
# conversion is done here rather than trusting a linear fill of the raw
# value, which is what made the previous meter look wrong.
METER_DB_MIN = -48.0   # bottom of the visible meter
METER_DB_MAX = 3.0     # a little headroom past 0dB for the red zone to read
METER_EPSILON = 1e-5   # floor before log10, so true silence doesn't hit -inf

# Colors read directly out of Ableton's own shipped theme files
# (Contents/App-Resources/Themes/*.ask, <StandardVuMeter> block) -- confirmed
# identical across every theme variant, so this isn't a per-theme guess.
METER_GREEN = (0x00, 0xf7, 0x58)
METER_YELLOW = (0xff, 0xd1, 0x00)
METER_RED = (0xff, 0x0a, 0x0a)
# dB breakpoints aren't stored in the theme file (Live's engine hardcodes
# them) -- these follow standard console-meter convention and match Live's
# observed behaviour: solid green well under 0dB, yellow as a warning band
# approaching it, red at and above unity.
METER_YELLOW_START_DB = -12.0
METER_RED_START_DB = 0.0

REFRESH_DEBOUNCE = 0.15   # quiet period before a full redraw after nav ticks
# Measured directly rather than guessed: Live pushes meter updates every
# ~23.5ms on average (min 12.5ms) during sustained audio, while one strip
# zone's full render+JPEG-encode+USB-write cycle costs ~2.6ms average (see
# strip_timing_probe.py). The old 60ms cap was the actual bottleneck -- Live
# was handing over fresh data more than twice as fast as the loop checked
# for it. 20ms keeps pace with Live's real cadence with large headroom left
# in the per-frame write budget, without chasing sub-perceptible gains
# below Live's own ~12.5ms floor.
STRIP_FRAME_INTERVAL = 0.02  # ~50 fps

# Knob pushes that emit MIDI instead of touching Live directly. CC 85/86 sit
# in the MIDI spec's undefined block, so nothing standard competes for them.
MIDI_PORT_NAME = "Mirabox N4"
KNOB_PUSH_CC = {3: 89, 4: 90}
MIDI_CHANNEL = 0  # 0 == channel 1 in Live's UI
PULSE_GAP = 0.05  # seconds between the 127 and the 0 of one button press

# CCs that latch (alternate 127 / 0 per press) instead of sending a momentary
# one-shot pulse. Live holds a CC "on" while it is >= 64, so state controls --
# Record, arm, solo, mute, a device on/off -- need latching or they only blink.
# One-shot triggers (Play, Stop, scene launch) want the momentary default.
#
# EDIT THIS LINE to change behaviour. Tap zones are 85-88, knob pushes 89-90.
#   {85, 86, 87, 88}  taps latch      (good for Record / arm / mute)
#   set()             everything one-shot trigger
CC_LATCH = set()  # {85, 86, 87, 88}

class MidiBridge:
    """Opens a virtual MIDI source so Live (and anything else) can see the
    N4's spare knob pushes as ordinary CC messages to MIDI-map."""

    def __init__(self, port_name=MIDI_PORT_NAME):
        self.out = None
        self._values = {}
        try:
            self.out = rtmidi.MidiOut()
            self.out.open_virtual_port(port_name)
            print(f"MIDI: virtual port '{port_name}' open")
        except Exception as e:
            print(f"MIDI: could not open virtual port ({e}); CC sends disabled")
            self.out = None

    def send_cc(self, cc, channel=MIDI_CHANNEL):
        """Two behaviours, because Live needs different things per target.

        MOMENTARY (default): 127 then 0, exactly what a hardware button
        sends. Right for one-shot triggers -- Play, Stop, scene launch --
        which fire on the rise and need the value back at 0 to fire again.

        LATCHING (CCs listed in CC_LATCH): alternates 127 / 0 across presses.
        Live reads a CC as held while it is >= 64, so a momentary pulse on a
        state control like Record arms it and disarms it milliseconds later
        -- the button just blinks. Latching holds the state until the next
        press, which is what a toggle needs.
        """
        if self.out is None:
            return None
        status = 0xB0 | channel

        if cc in CC_LATCH:
            value = 0 if self._values.get(cc) else 127
            self._values[cc] = value
            try:
                self.out.send_message([status, cc, value])
            except Exception as e:
                print(f"[midi error] {e}")
                return None
            return value

        def pulse():
            try:
                self.out.send_message([status, cc, 127])
                time.sleep(PULSE_GAP)
                self.out.send_message([status, cc, 0])
            except Exception as e:
                print(f"[midi error] {e}")

        threading.Thread(target=pulse, daemon=True).start()
        return 127

    def close(self):
        if self.out is not None:
            try:
                self.out.close_port()
            except Exception:
                pass


class OSCBridge:
    """One-shot request/reply (query / query_many) plus standing
    subscriptions (add_listener) for AbletonOSC's start_listen push
    notifications, which arrive unsolicited on the same receive port.

    Pending queries are keyed by (address, params), not address alone:
    several concurrent queries can share an address (volume for different
    track_ids) and must not clobber each other. AbletonOSC echoes the
    request params as the leading reply args, so that pair identifies
    which request a reply belongs to.
    """

    def __init__(self, send_port=11000, recv_port=11001):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", send_port)
        self._pending = {}
        self._listeners = {}
        self._lock = threading.Lock()

        disp = Dispatcher()
        disp.set_default_handler(self._on_message)
        self.server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", recv_port), disp)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _on_message(self, address, *args):
        with self._lock:
            matched_key = None
            for key in self._pending:
                key_addr, key_prefix = key
                if key_addr == address and tuple(args[:len(key_prefix)]) == key_prefix:
                    matched_key = key
                    break
            ev = self._pending.pop(matched_key, None) if matched_key else None
            listeners = list(self._listeners.get(address, []))
        if ev is not None:
            ev["args"] = args
            ev["event"].set()
        for callback in listeners:
            try:
                callback(*args)
            except Exception as e:
                print(f"[listener error] {e}")

    def query(self, address, params=(), timeout=0.3):
        return self.query_many([(address, params)], timeout=timeout)[0]

    def query_many(self, requests, timeout=0.3):
        """Fire all requests concurrently, then wait -- wall-clock cost is
        roughly the slowest single round-trip, not the sum of them."""
        entries = []
        for address, params in requests:
            key = (address, tuple(params))
            ev = {"event": threading.Event(), "args": None}
            with self._lock:
                self._pending[key] = ev
            entries.append((key, ev))

        for address, params in requests:
            self.client.send_message(address, list(params))

        deadline = time.time() + timeout
        results = []
        for key, ev in entries:
            ev["event"].wait(max(0.0, deadline - time.time()))
            with self._lock:
                self._pending.pop(key, None)
            results.append(ev["args"])
        return results

    def send(self, address, params=()):
        self.client.send_message(address, list(params))

    def add_listener(self, address, callback):
        with self._lock:
            self._listeners.setdefault(address, []).append(callback)


def color_int_to_rgb(color_int):
    return ((color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF, color_int & 0xFF)


def contrast_text_color(rgb):
    r, g, b = rgb
    return (20, 20, 20) if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else (240, 240, 240)


_fonts = {}


def font(size):
    if size not in _fonts:
        try:
            _fonts[size] = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except Exception:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def wrap_text(draw, text, f, max_width, max_lines=3):
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=f) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


class DeviceIO:
    """Serialises every device write behind one lock. Writes originate from
    the HID read thread (buttons), the OSC listener thread (Live selection
    changes), and the strip repaint thread."""

    def __init__(self, device):
        self.device = device
        self._lock = threading.Lock()

    def _write(self, logical_key, img, tag):
        with self._lock:
            try:
                path = f"/tmp/n4_img_{logical_key}.jpg"
                img.save(path, quality=90)
                self.device.set_key_image(logical_key, path)
                self.device.refresh()
            except Exception as e:
                print(f"[{tag} error] {e}")

    def write_clip_key(self, logical_key, rgb, text=""):
        img = Image.new("RGB", (KEY_W, KEY_H), rgb)
        if text:
            draw = ImageDraw.Draw(img)
            f = font(16)
            lines = wrap_text(draw, text, f, max_width=KEY_W - 12)
            fill = contrast_text_color(rgb)
            y = (KEY_H - len(lines) * 18) // 2
            for line in lines:
                w = draw.textlength(line, font=f)
                draw.text(((KEY_W - w) / 2, y), line, font=f, fill=fill)
                y += 18
        self._write(logical_key, img, "clip key")

    def write_strip_zone(self, zone, img):
        self._write(STRIP_ZONE_LOGICAL[zone], img, "strip zone")

    def write_leds(self, colors):
        """No-op on devices without RGB knobs (StreamDock.set_single_led_color
        itself checks feature_option.hasRGBLed) -- safe to call unconditionally
        so this works on both the base N4 and N4 Pro without a device check."""
        with self._lock:
            try:
                self.device.set_single_led_color(colors)
            except Exception as e:
                print(f"[led error] {e}")

    def write_led_brightness(self, percent):
        with self._lock:
            try:
                self.device.set_led_brightness(percent)
            except Exception as e:
                print(f"[led brightness error] {e}")


def split_db_text(db_text, volume):
    """Live hands back strings like '-6.0 dB', '-1.498 dB', '-inf dB' -- the
    precision varies, so round to one decimal. Falls back to a percentage if
    Live supplied nothing (e.g. AbletonOSC not reloaded, since Remote Scripts
    only load at Live's launch)."""
    text = (db_text or "").strip()
    if not text:
        return f"{int(round(volume * 100))}%", ""

    unit = ""
    if text.lower().endswith("db"):
        text, unit = text[:-2].strip(), "dB"

    if "inf" in text.lower():
        return ("-∞" if text.lstrip().startswith("-") else "∞"), unit
    try:
        return f"{float(text):.1f}", unit
    except ValueError:
        return text, unit


CC_ROW_Y = 92          # single compact CC reference line, bottom of every zone
CC_ROW_COLOR = (120, 120, 145)


def zone_tap_cc(zone):
    """Strip zone 1-4 -> the tap CC for that quarter (codes sort left to right)."""
    codes = sorted(TAP_ZONE_CC)
    return TAP_ZONE_CC[codes[zone - 1]] if 1 <= zone <= len(codes) else None


def draw_cc_row(draw, tap_cc, push_cc=None):
    """One tight line so the CC reference costs a single row of pixels."""
    text = f"tap {tap_cc}" + (f"  push {push_cc}" if push_cc else "")
    size = 13
    while size > 9 and draw.textlength(text, font=font(size)) > STRIP_W - 12:
        size -= 1
    draw.text((7, CC_ROW_Y), text, font=font(size), fill=CC_ROW_COLOR)


def amplitude_to_db(level):
    return 20.0 * math.log10(max(level, METER_EPSILON))


def db_to_fill_fraction(db):
    """Log-scale meter position, 0 (silence) to 1 (top of the visible range)."""
    span = METER_DB_MAX - METER_DB_MIN
    return max(0.0, min(1.0, (db - METER_DB_MIN) / span))


def lerp_color(c0, c1, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))


def meter_color(db):
    """Gradual green -> yellow -> red, using Ableton's own gradient stops
    and dB breakpoints -- not a hard-cutoff three-zone flag."""
    if db <= METER_YELLOW_START_DB:
        return METER_GREEN
    if db <= METER_RED_START_DB:
        t = (db - METER_YELLOW_START_DB) / (METER_RED_START_DB - METER_YELLOW_START_DB)
        return lerp_color(METER_GREEN, METER_YELLOW, t)
    t = min(1.0, (db - METER_RED_START_DB) / 3.0)
    return lerp_color(METER_YELLOW, METER_RED, t)


def draw_meter_bar(draw, x0, x1, y0, y1, level):
    """One log-scaled, gradient-colored meter bar. Drawn as thin vertical
    slivers rather than one flat rectangle fill, so the green->yellow->red
    transition is visible as an actual gradient across the bar, matching
    Ableton's own meter (OnlyMinimumToMaximum=false in its theme file --
    i.e. a true gradient, not a single threshold color)."""
    draw.rectangle([x0, y0, x1, y1], outline=(55, 55, 55))
    db = amplitude_to_db(level)
    fraction = db_to_fill_fraction(db)
    fill_px = int((x1 - x0) * fraction)
    if fill_px <= 0:
        return
    step = 2
    for sx in range(x0, x0 + fill_px, step):
        seg_db = METER_DB_MIN + (METER_DB_MAX - METER_DB_MIN) * ((sx - x0) / (x1 - x0))
        draw.rectangle([sx, y0, min(sx + step, x0 + fill_px), y1],
                       fill=meter_color(seg_db))


def render_volume_zone(ch_num, name, volume, muted, meter_l=0.0, meter_r=0.0,
                       db_text="", tap_cc=None):
    """One 176x112 strip zone, drawn at native size -- no slicing, no scaling.

    Two thin bars are live output meters (post-fader signal, updating in
    real time) -- L on top, R below, matching Ableton's own mixer which
    always shows both regardless of whether the source was recorded mono
    (Live's engine is stereo throughout, so there's no reliable "is this
    track mono" flag to key off -- showing L/R unconditionally is exactly
    what Live itself does). Log-scaled in dB, not a linear fill of the raw
    amplitude, and colored with Ableton's own gradient stops.

    The small triangle beneath is a separate, static marker for the
    fader's own position -- meter and fader are different things and can
    disagree, e.g. a fader at 0dB with no signal shows empty bars under a
    triangle sitting mid-scale.
    """
    img = Image.new("RGB", (STRIP_W, STRIP_H), (12, 12, 12))
    draw = ImageDraw.Draw(img)

    if ch_num is None:
        draw.text((10, 40), "--", font=font(24), fill=(90, 90, 90))
        if tap_cc:
            draw_cc_row(draw, tap_cc)
        return img

    # Header row: channel number + dB/MUTE on the right, both kept compact
    # so the track name below can run bigger.
    draw.text((7, 1), f"Ch{ch_num}", font=font(18), fill=(205, 205, 205))

    number, unit = split_db_text(db_text, volume)
    db_label = f"{number}{unit}"
    dsize = 15
    while dsize > 11 and draw.textlength(db_label, font=font(dsize)) > 66:
        dsize -= 1
    draw.text((STRIP_W - 7 - draw.textlength(db_label, font=font(dsize)), 1),
              db_label, font=font(dsize), fill=(165, 165, 165))
    if muted:
        draw.text((STRIP_W - 46, 20), "MUTE", font=font(14), fill=(225, 85, 85))

    # Track name: the biggest element now, given the room freed up elsewhere.
    nsize = 26
    while nsize > 18 and draw.textlength(name, font=font(nsize)) > STRIP_W - 14:
        nsize -= 1
    draw.text((7, 21), name[:14], font=font(nsize), fill=(245, 245, 245))

    # Two log-scaled meters, L over R.
    track_x0, track_x1 = 7, STRIP_W - 7
    bar_h = 7
    gap = 2
    l_y0 = 50
    l_y1 = l_y0 + bar_h
    r_y0 = l_y1 + gap
    r_y1 = r_y0 + bar_h

    if not muted:
        draw_meter_bar(draw, track_x0, track_x1, l_y0, l_y1, meter_l)
        draw_meter_bar(draw, track_x0, track_x1, r_y0, r_y1, meter_r)
    else:
        draw.rectangle([track_x0, l_y0, track_x1, l_y1], outline=(55, 55, 55))
        draw.rectangle([track_x0, r_y0, track_x1, r_y1], outline=(55, 55, 55))

    # 0 dB reference line through both bars.
    zero_frac = db_to_fill_fraction(0.0)
    zx = track_x0 + (track_x1 - track_x0) * zero_frac
    draw.line([zx, l_y0, zx, r_y1], fill=(120, 120, 60), width=1)

    # Fader-position marker beneath both bars. Must share the meter's log-dB
    # x scale, not the fader's own raw 0-1 value -- those are two different
    # curves (fader unity sits at the linear-ish value 0.85; the meter's
    # 0dB sits at log-scaled fraction ~0.94), so plotting the raw fader
    # value directly against the meter's axis put the two "0dB" landmarks
    # ~15px apart. Converting through the fader's own dB reading (already
    # parsed above) keeps both markers on one consistent scale.
    if unit == "dB" and number not in ("-∞", "∞"):
        try:
            fader_frac = db_to_fill_fraction(float(number))
        except ValueError:
            fader_frac = zero_frac if abs(volume - ZERO_DB_VALUE) < 0.01 else volume
    elif number == "-∞":
        fader_frac = 0.0
    else:
        # No dB reading available yet (e.g. AbletonOSC not reloaded) --
        # degrade to the raw fader value rather than crash.
        fader_frac = volume
    fx = track_x0 + (track_x1 - track_x0) * max(0.0, min(1.0, fader_frac))
    tri_y = r_y1 + 3
    draw.polygon([(fx - 4, tri_y + 5), (fx + 4, tri_y + 5), (fx, tri_y)],
                 fill=(210, 210, 220))

    if tap_cc:
        draw_cc_row(draw, tap_cc)
    return img


def render_nav_zone(label, current, total, tap_cc=None, push_cc=None):
    img = Image.new("RGB", (STRIP_W, STRIP_H), (12, 12, 12))
    draw = ImageDraw.Draw(img)
    draw.text((7, 2), label, font=font(18), fill=(155, 165, 225))
    draw.text((7, 28), f"{current}/{total}", font=font(32), fill=(240, 240, 240))
    if tap_cc:
        draw_cc_row(draw, tap_cc, push_cc)
    return img


class State:
    def __init__(self, osc: OSCBridge):
        self.osc = osc
        reply = osc.query("/live/song/get/num_tracks", timeout=1.0)
        self.num_tracks = reply[0] if reply else KEY_COLUMNS
        scenes = osc.query("/live/song/get/num_scenes", timeout=1.0)
        self.num_scenes = scenes[0] if scenes else 1

        base = osc.query("/live/view/get/selected_track", timeout=1.0)
        self.base = base[0] if base else 0
        scroll = osc.query("/live/view/get/selected_scene", timeout=1.0)
        self.scroll_scene = scroll[0] if scroll else 0

        self.volumes = [0.0] * KEY_COLUMNS
        self.meters_l = [0.0] * KEY_COLUMNS
        self.meters_r = [0.0] * KEY_COLUMNS
        self.mutes = [False] * KEY_COLUMNS
        self.track_names = ["--"] * KEY_COLUMNS
        self.clip_colors = [EMPTY_CLIP_COLOR] * KEY_COLUMNS
        self.clip_names = [""] * KEY_COLUMNS
        self.clip_playing = [False] * KEY_COLUMNS
        self.clip_triggered = [False] * KEY_COLUMNS
        self.row2_colors = [EMPTY_CLIP_COLOR] * KEY_COLUMNS
        self.row2_names = [""] * KEY_COLUMNS
        self.row2_playing = [False] * KEY_COLUMNS
        self.row2_triggered = [False] * KEY_COLUMNS
        self.row2_scene = 0
        # [row1, row2] scene launch buttons on keys 5 and 10. "Active" here
        # means at least one of the 4 currently-visible tracks has that
        # scene's clip playing/triggered -- reflects what's shown on the
        # hardware right now, not the whole song's tracks.
        self.scene_names = ["", ""]
        self.scene_colors = [SCENE_DEFAULT_COLOR, SCENE_DEFAULT_COLOR]
        self.scene_playing = [False, False]
        self.scene_triggered = [False, False]

    def recompute_scene_activity(self):
        self.scene_playing[0] = any(self.clip_playing)
        self.scene_triggered[0] = any(self.clip_triggered)
        self.scene_playing[1] = any(self.row2_playing)
        self.scene_triggered[1] = any(self.row2_triggered)

    def track_ids(self):
        return [self.base + i for i in range(KEY_COLUMNS) if self.base + i < self.num_tracks]

    def refresh_page(self):
        """AbletonOSC only answers once per Live's own Remote Script
        scheduler tick (~100ms), no matter how many messages arrive within
        that window -- confirmed empirically (every stage below cost the
        same ~100ms whether it held 2 requests or 11). So the real latency
        cost is the *number of sequential round trips*, not their size.
        This does it in two: one for everything that doesn't depend on
        this call's own results (dimensions, per-column track props,
        has_clip, scene props), and a second for clip color/name/playing
        /triggered -- which can only be requested once has_clip says which
        slots are non-empty. Previously this was ~14 sequential round
        trips (one per property per row) and took ~1.5s; this cuts it to
        ~200ms.

        The track/scene window used to build round 1's requests comes
        from the *previous* refresh's counts, not this call's fresh ones
        (those arrive in the same round trip, too late to gate it) --
        harmless staleness, since num_tracks/num_scenes only actually
        change when tracks/scenes are added or removed in Live, and any
        request for a since-removed id just times out that one slot
        without blocking the rest of the batch.
        """
        t_start = time.time()

        def lap(label, t0):
            elapsed = (time.time() - t0) * 1000
            if elapsed > 20:
                print(f"[refresh timing] {label}: {elapsed:.0f}ms")
            return time.time()

        ids = self.track_ids()
        scroll_scene = self.scroll_scene
        row2_scene = min(scroll_scene + 1, max(0, self.num_scenes - 1))
        need_row2 = row2_scene != scroll_scene
        scene_rows = [scroll_scene, row2_scene]

        reqs1 = [("/live/song/get/num_tracks", []), ("/live/song/get/num_scenes", [])]
        reqs1 += [("/live/track/get/volume", [t]) for t in ids]
        reqs1 += [("/live/track/get/name", [t]) for t in ids]
        reqs1 += [("/live/track/get/mute", [t]) for t in ids]
        reqs1 += [("/live/clip_slot/get/has_clip", [t, scroll_scene]) for t in ids]
        if need_row2:
            reqs1 += [("/live/clip_slot/get/has_clip", [t, row2_scene]) for t in ids]
        reqs1 += [("/live/scene/get/name", [s]) for s in scene_rows]
        reqs1 += [("/live/scene/get/color", [s]) for s in scene_rows]

        replies1 = self.osc.query_many(reqs1)
        t_prev = lap("round1", t_start)

        i = 0
        counts = replies1[i:i + 2]; i += 2
        vols = replies1[i:i + len(ids)]; i += len(ids)
        names = replies1[i:i + len(ids)]; i += len(ids)
        mutes = replies1[i:i + len(ids)]; i += len(ids)
        has1 = replies1[i:i + len(ids)]; i += len(ids)
        has2 = []
        if need_row2:
            has2 = replies1[i:i + len(ids)]; i += len(ids)
        scene_name_reply = replies1[i:i + len(scene_rows)]; i += len(scene_rows)
        scene_color_reply = replies1[i:i + len(scene_rows)]; i += len(scene_rows)

        if counts[0] and len(counts[0]) >= 1:
            self.num_tracks = counts[0][0]
        if counts[1] and len(counts[1]) >= 1:
            self.num_scenes = counts[1][0]
        self.base = max(0, min(self.base, max(0, self.num_tracks - 1)))
        self.scroll_scene = max(0, min(self.scroll_scene, max(0, self.num_scenes - 1)))
        self.row2_scene = min(self.scroll_scene + 1, max(0, self.num_scenes - 1))

        for i2, scene_id in enumerate(scene_rows):
            nm = scene_name_reply[i2]
            cl = scene_color_reply[i2]
            label = nm[1] if nm and len(nm) >= 2 and nm[1] else f"Scene {scene_id + 1}"
            self.scene_names[i2] = label
            self.scene_colors[i2] = (
                color_int_to_rgb(cl[1]) if cl and len(cl) >= 2 and cl[1]
                else SCENE_DEFAULT_COLOR
            )

        def build_clip_reqs(has, scene):
            idx, creqs, nreqs, preqs, treqs = [], [], [], [], []
            for pos, tid in enumerate(ids):
                h = has[pos] if pos < len(has) else None
                if h and len(h) >= 3 and h[2]:
                    idx.append(pos)
                    creqs.append(("/live/clip/get/color", [tid, scene]))
                    nreqs.append(("/live/clip/get/name", [tid, scene]))
                    preqs.append(("/live/clip_slot/get/is_playing", [tid, scene]))
                    treqs.append(("/live/clip_slot/get/is_triggered", [tid, scene]))
            return idx, creqs, nreqs, preqs, treqs

        idx1, creqs1, nreqs1, preqs1, treqs1 = build_clip_reqs(has1, scroll_scene)
        idx2, creqs2, nreqs2, preqs2, treqs2 = (
            build_clip_reqs(has2, row2_scene) if need_row2 else ([], [], [], [], [])
        )

        reqs2 = creqs1 + nreqs1 + preqs1 + treqs1 + creqs2 + nreqs2 + preqs2 + treqs2
        replies2 = self.osc.query_many(reqs2) if reqs2 else []
        t_prev = lap("round2", t_prev)

        j = 0
        colors1 = replies2[j:j + len(creqs1)]; j += len(creqs1)
        names1 = replies2[j:j + len(nreqs1)]; j += len(nreqs1)
        playing1 = replies2[j:j + len(preqs1)]; j += len(preqs1)
        triggered1 = replies2[j:j + len(treqs1)]; j += len(treqs1)
        colors2 = replies2[j:j + len(creqs2)]; j += len(creqs2)
        names2 = replies2[j:j + len(nreqs2)]; j += len(nreqs2)
        playing2 = replies2[j:j + len(preqs2)]; j += len(preqs2)
        triggered2 = replies2[j:j + len(treqs2)]; j += len(treqs2)

        for i in range(KEY_COLUMNS):
            if i < len(ids):
                tid = ids[i]
                v = vols[i] if i < len(vols) else None
                n = names[i] if i < len(names) else None
                m = mutes[i] if i < len(mutes) else None
                self.volumes[i] = v[1] if v and len(v) >= 2 else 0.0
                self.track_names[i] = n[1] if n and len(n) >= 2 else f"Track {tid + 1}"
                self.mutes[i] = bool(m[1]) if m and len(m) >= 2 else False
            else:
                self.volumes[i], self.mutes[i], self.track_names[i] = 0.0, False, "--"
            self.clip_colors[i], self.clip_names[i] = EMPTY_CLIP_COLOR, ""
            self.clip_playing[i], self.clip_triggered[i] = False, False
            self.row2_colors[i], self.row2_names[i] = EMPTY_CLIP_COLOR, ""
            self.row2_playing[i], self.row2_triggered[i] = False, False

        for pos, i in enumerate(idx1):
            c = colors1[pos] if pos < len(colors1) else None
            n = names1[pos] if pos < len(names1) else None
            p = playing1[pos] if pos < len(playing1) else None
            t = triggered1[pos] if pos < len(triggered1) else None
            self.clip_colors[i] = color_int_to_rgb(c[2]) if c and len(c) >= 3 else EMPTY_CLIP_COLOR
            self.clip_names[i] = n[2] if n and len(n) >= 3 else ""
            self.clip_playing[i] = bool(p and len(p) >= 3 and p[2])
            self.clip_triggered[i] = bool(t and len(t) >= 3 and t[2])

        for pos, i in enumerate(idx2):
            c = colors2[pos] if pos < len(colors2) else None
            n = names2[pos] if pos < len(names2) else None
            p = playing2[pos] if pos < len(playing2) else None
            t = triggered2[pos] if pos < len(triggered2) else None
            self.row2_colors[i] = color_int_to_rgb(c[2]) if c and len(c) >= 3 else EMPTY_CLIP_COLOR
            self.row2_names[i] = n[2] if n and len(n) >= 3 else ""
            self.row2_playing[i] = bool(p and len(p) >= 3 and p[2])
            self.row2_triggered[i] = bool(t and len(t) >= 3 and t[2])

        self.recompute_scene_activity()
        total = (time.time() - t_start) * 1000
        if total > 100:
            print(f"[refresh timing] TOTAL: {total:.0f}ms")


class StripPainter:
    """Repaints strip zones on a bounded schedule so fast knob turns can't
    flood the device with writes."""

    def __init__(self, io: DeviceIO, state: State):
        self.io = io
        self.state = state
        self._dirty = set()
        self._lock = threading.Lock()
        self._led_colors = [KNOB_LED_IDLE, KNOB_LED_IDLE, KNOB_LED_IDLE, KNOB_LED_IDLE]
        self.io.write_led_brightness(KNOB_LED_BRIGHTNESS)
        self.io.write_leds(self._led_colors)
        threading.Thread(target=self._loop, daemon=True).start()

    def mark(self, *zones):
        with self._lock:
            self._dirty.update(zones or (1, 2, 3, 4))

    def _render(self, zone):
        s = self.state
        if zone in VOLUME_KNOBS:
            col = zone - 1
            ids = s.track_ids()
            ch = ids[col] + 1 if col < len(ids) else None
            db_text = ""
            if ch is not None:
                # Ask Live for its own dB readout rather than reimplementing
                # its non-linear fader curve. Short timeout: this runs on the
                # paint thread and a stale frame beats a stalled one.
                reply = s.osc.query("/live/track/get/volume_string",
                                    [ids[col]], timeout=0.15)
                if reply and len(reply) >= 2:
                    db_text = str(reply[1])
            led = s.clip_colors[col]
            self._led_colors[col] = (
                dim_color(led, KNOB_LED_MUTE_DIM) if s.mutes[col] else led
            )
            return render_volume_zone(ch, s.track_names[col], s.volumes[col],
                                      s.mutes[col], meter_l=s.meters_l[col],
                                      meter_r=s.meters_r[col], db_text=db_text,
                                      tap_cc=zone_tap_cc(zone))
        if zone == 3:
            return render_nav_zone("TRACK", s.base + 1, s.num_tracks,
                                   zone_tap_cc(3), KNOB_PUSH_CC[3])
        return render_nav_zone("SCENE", s.scroll_scene + 1, s.num_scenes,
                               zone_tap_cc(4), KNOB_PUSH_CC[4])

    def _loop(self):
        while True:
            time.sleep(STRIP_FRAME_INTERVAL)
            with self._lock:
                zones, self._dirty = sorted(self._dirty), set()
            leds_changed = False
            for zone in zones:
                try:
                    self.io.write_strip_zone(zone, self._render(zone))
                    if zone in VOLUME_KNOBS:
                        leds_changed = True
                except Exception as e:
                    print(f"[strip {zone}] {e}")
            if leds_changed:
                self.io.write_leds(self._led_colors)


class KeyPainter:
    """Repaints clip/scene keys on the same bounded-schedule pattern as
    StripPainter, instead of writing straight from whichever thread noticed
    a change. Buttons pressed on the HID thread and clip-state updates
    arriving on the OSC listener thread both used to call a synchronous,
    locked device write directly -- contending with this same lock against
    the strip's own ~50/s background writes, which delayed the HID thread
    from reading the *next* button press. Marking dirty and letting one
    background thread own every key write removes that contention."""

    ALL_KEYS = (tuple(ROW1_KEY_TO_LOGICAL.values()) + tuple(ROW2_KEY_TO_LOGICAL.values())
                + (SCENE_KEY_LOGICAL["top5"], SCENE_KEY_LOGICAL["bot5"]))

    def __init__(self, io: DeviceIO, state: State):
        self.io = io
        self.state = state
        self._dirty = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def mark(self, *logical_keys):
        with self._lock:
            self._dirty.update(logical_keys or self.ALL_KEYS)

    def _render(self, logical_key, blink_on):
        s = self.state
        if logical_key in ROW1_LOGICAL_TO_COL:
            col = ROW1_LOGICAL_TO_COL[logical_key]
            color = clip_key_color(s.clip_colors[col], s.clip_playing[col],
                                   s.clip_triggered[col], blink_on, EMPTY_CLIP_COLOR)
            return color, s.clip_names[col]
        if logical_key in ROW2_LOGICAL_TO_COL:
            col = ROW2_LOGICAL_TO_COL[logical_key]
            color = clip_key_color(s.row2_colors[col], s.row2_playing[col],
                                   s.row2_triggered[col], blink_on, EMPTY_CLIP_COLOR)
            return color, s.row2_names[col]
        idx = 0 if logical_key == SCENE_KEY_LOGICAL["top5"] else 1
        color = clip_key_color(s.scene_colors[idx], s.scene_playing[idx],
                               s.scene_triggered[idx], blink_on)
        return color, s.scene_names[idx]

    def _triggered_keys(self):
        s = self.state
        keys = {logical for col, logical in COL_TO_ROW1_LOGICAL.items() if s.clip_triggered[col]}
        keys |= {logical for col, logical in COL_TO_ROW2_LOGICAL.items() if s.row2_triggered[col]}
        if s.scene_triggered[0]:
            keys.add(SCENE_KEY_LOGICAL["top5"])
        if s.scene_triggered[1]:
            keys.add(SCENE_KEY_LOGICAL["bot5"])
        return keys

    def _loop(self):
        while True:
            time.sleep(STRIP_FRAME_INTERVAL)
            # A triggered key keeps blinking every frame even if nothing else
            # marked it dirty this cycle -- otherwise the animation would
            # freeze on whatever phase it happened to be at when last drawn.
            blink_on = int(time.time() * BLINK_HZ * 2) % 2 == 0
            with self._lock:
                self._dirty.update(self._triggered_keys())
                keys, self._dirty = sorted(self._dirty), set()
            for logical_key in keys:
                try:
                    color, text = self._render(logical_key, blink_on)
                    self.io.write_clip_key(logical_key, color, text)
                except Exception as e:
                    print(f"[key {logical_key}] {e}")


def make_handler(state: State, io: DeviceIO, strip: StripPainter,
                 keys: KeyPainter, midi: MidiBridge):
    def push_ring():
        """Fire-and-forget, sent the instant the window moves. Previously this
        rode along at the end of refresh_all, so the ring waited out the
        150ms debounce plus a full round of state queries before moving."""
        state.osc.send("/live/view/set/session_highlight",
                       [state.base, state.scroll_scene, KEY_COLUMNS, RING_HEIGHT, 0])

    debounce_lock = threading.Lock()
    deadline = [0.0]
    running = [False]

    def refresh_all():
        state.refresh_page()
        keys.mark()
        strip.mark()
        sync_clip_listeners()
        sync_volume_listeners()
        push_ring()

    def schedule_refresh():
        """Coalesce rapid nav ticks into a single redraw once things settle,
        rather than a full refresh per tick."""
        with debounce_lock:
            deadline[0] = time.time() + REFRESH_DEBOUNCE
            if running[0]:
                return
            running[0] = True

        def worker():
            while True:
                with debounce_lock:
                    remaining = deadline[0] - time.time()
                    if remaining <= 0:
                        running[0] = False
                        break
                time.sleep(min(remaining, 0.05))
            refresh_all()

        threading.Thread(target=worker, daemon=True).start()

    def on_track_selected(track_index):
        if track_index == state.base:
            return
        state.base = track_index
        push_ring()
        print(f"[selection] leftmost track -> {track_index + 1}")
        schedule_refresh()

    # Tracks we currently hold volume/meter subscriptions for, so faders
    # moved in Live -- and the live audio meter -- push straight to the
    # strip rather than only updating on the next refresh.
    volume_subs = set()

    METER_PROPS = ("output_meter_left", "output_meter_right")

    def sync_volume_listeners():
        wanted = set(state.track_ids()[:len(VOLUME_KNOBS)])
        for tid in volume_subs - wanted:
            state.osc.send("/live/track/stop_listen/volume", [tid])
            for prop in METER_PROPS:
                state.osc.send(f"/live/track/stop_listen/{prop}", [tid])
        for tid in wanted - volume_subs:
            state.osc.send("/live/track/start_listen/volume", [tid])
            for prop in METER_PROPS:
                state.osc.send(f"/live/track/start_listen/{prop}", [tid])
            # New track on this column: drop any stale reading from whatever
            # track used to be there rather than waiting for the first update.
            for col in range(len(VOLUME_KNOBS)):
                ids = state.track_ids()
                if col < len(ids) and ids[col] == tid:
                    state.meters_l[col] = 0.0
                    state.meters_r[col] = 0.0
        volume_subs.clear()
        volume_subs.update(wanted)

    def stop_all_listeners():
        """Unsubscribe everything before exit. Without this, killed sessions
        (SIGTERM skips Python's finally blocks by default) leave listeners
        running forever inside Live's own AbletonOSC instance -- they aren't
        tied to this process, only to Live staying open. Harmless to
        function (updates for tracks nobody's displaying are just ignored)
        but they accumulate and spam the OSC port indefinitely."""
        for tid in volume_subs:
            state.osc.send("/live/track/stop_listen/volume", [tid])
            for prop in METER_PROPS:
                state.osc.send(f"/live/track/stop_listen/{prop}", [tid])
        volume_subs.clear()
        for tid in clip_track_subs:
            state.osc.send("/live/track/stop_listen/playing_slot_index", [tid])
            state.osc.send("/live/track/stop_listen/fired_slot_index", [tid])
        clip_track_subs.clear()
        state.osc.send("/live/view/stop_listen/selected_track", [])
        state.osc.send("/live/view/stop_listen/selected_scene", [])

    def on_volume_changed(*args):
        if len(args) < 2:
            return
        tid, value = args[0], args[1]
        ids = state.track_ids()
        for col in range(len(VOLUME_KNOBS)):
            if col < len(ids) and ids[col] == tid:
                if abs(state.volumes[col] - value) > 1e-6:
                    state.volumes[col] = value
                    strip.mark(col + 1)
                return

    def make_meter_handler(target_list):
        def on_meter_changed(*args):
            if len(args) < 2:
                return
            tid, value = args[0], args[1]
            ids = state.track_ids()
            for col in range(len(VOLUME_KNOBS)):
                if col < len(ids) and ids[col] == tid:
                    target_list[col] = value
                    strip.mark(col + 1)
                    return
        return on_meter_changed

    on_meter_left_changed = make_meter_handler(state.meters_l)
    on_meter_right_changed = make_meter_handler(state.meters_r)

    # Live clip play-state subscriptions, one per currently visible TRACK
    # (not per slot -- clip_slot.is_playing/is_triggered turned out not to
    # support listening in Live's API at all, confirmed empirically; but
    # track.playing_slot_index/fired_slot_index do, and are actually a
    # cleaner fit: comparing the returned scene index to whichever row
    # we're showing tells us if that row's clip is playing, for free,
    # with a quarter of the subscriptions). This is a live-update path
    # only -- refresh_page()'s one-shot has_clip/is_playing/is_triggered
    # queries (which DO work as plain gets) remain the source of truth on
    # every full refresh; this just closes the gap between refreshes.
    clip_track_subs = set()   # {track_id, ...} currently subscribed
    track_slot_state = {}     # track_id -> {"playing": int, "fired": int}

    def sync_clip_listeners():
        wanted = set(state.track_ids())
        for tid in clip_track_subs - wanted:
            state.osc.send("/live/track/stop_listen/playing_slot_index", [tid])
            state.osc.send("/live/track/stop_listen/fired_slot_index", [tid])
            track_slot_state.pop(tid, None)
        for tid in wanted - clip_track_subs:
            state.osc.send("/live/track/start_listen/playing_slot_index", [tid])
            state.osc.send("/live/track/start_listen/fired_slot_index", [tid])
        clip_track_subs.clear()
        clip_track_subs.update(wanted)

    def apply_track_slot_state(tid):
        st = track_slot_state.get(tid, {})
        playing_idx = st.get("playing", -2)
        fired_idx = st.get("fired", -1)
        scene_activity_changed = False
        for col, t in enumerate(state.track_ids()):
            if t != tid:
                continue
            playing1 = playing_idx == state.scroll_scene
            triggered1 = fired_idx == state.scroll_scene
            if state.clip_playing[col] != playing1 or state.clip_triggered[col] != triggered1:
                state.clip_playing[col] = playing1
                state.clip_triggered[col] = triggered1
                keys.mark(COL_TO_ROW1_LOGICAL[col])
                scene_activity_changed = True
            playing2 = playing_idx == state.row2_scene
            triggered2 = fired_idx == state.row2_scene
            if state.row2_playing[col] != playing2 or state.row2_triggered[col] != triggered2:
                state.row2_playing[col] = playing2
                state.row2_triggered[col] = triggered2
                keys.mark(COL_TO_ROW2_LOGICAL[col])
                scene_activity_changed = True
        if scene_activity_changed:
            prev_scene_playing = list(state.scene_playing)
            prev_scene_triggered = list(state.scene_triggered)
            state.recompute_scene_activity()
            if state.scene_playing[0] != prev_scene_playing[0] or state.scene_triggered[0] != prev_scene_triggered[0]:
                keys.mark(SCENE_KEY_LOGICAL["top5"])
            if state.scene_playing[1] != prev_scene_playing[1] or state.scene_triggered[1] != prev_scene_triggered[1]:
                keys.mark(SCENE_KEY_LOGICAL["bot5"])

    def make_track_slot_handler(key):
        def handler(*args):
            if len(args) < 2:
                return
            tid, value = args[0], args[1]
            track_slot_state.setdefault(tid, {})[key] = value
            apply_track_slot_state(tid)
        return handler

    on_playing_slot_changed = make_track_slot_handler("playing")
    on_fired_slot_changed = make_track_slot_handler("fired")

    def on_scene_selected(scene_index):
        if scene_index == state.scroll_scene:
            return
        state.scroll_scene = scene_index
        push_ring()
        print(f"[selection] scene -> {scene_index + 1}")
        schedule_refresh()

    def on_key(name, pressed):
        if not pressed:
            return
        ids = state.track_ids()
        if name == "top5":
            state.osc.send("/live/scene/fire", [state.scroll_scene])
            print(f"[fire scene] {state.scroll_scene + 1}")
            return
        if name == "bot5":
            state.osc.send("/live/scene/fire", [state.row2_scene])
            print(f"[fire scene] {state.row2_scene + 1}")
            return
        if name in ROW1_KEY_TO_COLUMN:
            col = ROW1_KEY_TO_COLUMN[name]
            if col < len(ids):
                state.osc.send("/live/clip/fire", [ids[col], state.scroll_scene])
                print(f"[fire] track {ids[col] + 1}, scene {state.scroll_scene + 1}")
                if state.clip_colors[col] != EMPTY_CLIP_COLOR:
                    # Optimistic: light up immediately rather than waiting for
                    # the listener round-trip. The listener corrects this
                    # shortly after if firing didn't actually start playback.
                    state.clip_playing[col] = True
                    state.clip_triggered[col] = False
                    keys.mark(COL_TO_ROW1_LOGICAL[col])
                    state.recompute_scene_activity()
                    keys.mark(SCENE_KEY_LOGICAL["top5"])
        elif name in ROW2_KEY_TO_COLUMN:
            col = ROW2_KEY_TO_COLUMN[name]
            if col < len(ids):
                state.osc.send("/live/clip/fire", [ids[col], state.row2_scene])
                print(f"[fire] track {ids[col] + 1}, scene {state.row2_scene + 1}")
                if state.row2_colors[col] != EMPTY_CLIP_COLOR:
                    state.row2_playing[col] = True
                    state.row2_triggered[col] = False
                    keys.mark(COL_TO_ROW2_LOGICAL[col])
                    state.recompute_scene_activity()
                    keys.mark(SCENE_KEY_LOGICAL["bot5"])

    def on_knob_rotate(knob, direction):
        if knob in VOLUME_KNOBS:
            col = knob - 1
            ids = state.track_ids()
            if col >= len(ids):
                return
            old_vol = state.volumes[col]
            raw = old_vol + direction * VOLUME_STEP
            # If this tick steps over unity, stop exactly on it -- a fixed step
            # size would otherwise skip straight past 0.0 dB and never hit it.
            if (old_vol - ZERO_DB_VALUE) * (raw - ZERO_DB_VALUE) < 0:
                new_vol = ZERO_DB_VALUE
            else:
                new_vol = max(0.0, min(1.0, raw))
            state.volumes[col] = new_vol
            state.osc.send("/live/track/set/volume", [ids[col], new_vol])
            strip.mark(knob)
        elif knob == 3:
            new_base = max(0, min(max(0, state.num_tracks - 1), state.base + direction))
            if new_base != state.base:
                state.base = new_base
                state.osc.send("/live/view/set/selected_track", [new_base])
                push_ring()
                strip.mark(3)
                schedule_refresh()
        elif knob == 4:
            new_scene = max(0, min(max(0, state.num_scenes - 1), state.scroll_scene + direction))
            if new_scene != state.scroll_scene:
                state.scroll_scene = new_scene
                state.osc.send("/live/view/set/selected_scene", [new_scene])
                push_ring()
                strip.mark(4)
                schedule_refresh()

    last_tap = {code: 0.0 for code in TAP_ZONE_CC}
    tap_zone_order = sorted(TAP_ZONE_CC)

    def on_tap(code):
        now = time.time()
        if now - last_tap[code] < TAP_DEBOUNCE:
            return
        last_tap[code] = now
        cc = TAP_ZONE_CC[code]
        zone = tap_zone_order.index(code) + 1
        if midi.send_cc(cc) is None:
            print(f"[midi] tap zone {zone} -> CC {cc} FAILED (port not open)")
        else:
            print(f"[midi] tap zone {zone} -> CC {cc} (127 then 0)")

    last_swipe = [0.0]

    def on_swipe(direction):
        now = time.time()
        if now - last_swipe[0] < SWIPE_DEBOUNCE:
            return
        last_swipe[0] = now
        new_base = max(0, min(max(0, state.num_tracks - 1),
                              state.base + direction * SWIPE_TRACKS))
        if new_base == state.base:
            return
        state.base = new_base
        state.osc.send("/live/view/set/selected_track", [new_base])
        push_ring()
        which = "left to right" if direction > 0 else "right to left"
        print(f"[swipe] {which} -> tracks from {new_base + 1}")
        strip.mark(3)
        schedule_refresh()

    def on_knob_push(knob):
        if knob in KNOB_PUSH_CC:
            cc = KNOB_PUSH_CC[knob]
            value = midi.send_cc(cc)
            if value is None:
                print(f"[midi] knob {knob} push -> CC {cc} FAILED (port not open)")
            else:
                print(f"[midi] knob {knob} push -> CC {cc} (127 then 0)")
            return
        col = knob - 1
        ids = state.track_ids()
        if col >= len(ids):
            return
        new_mute = not state.mutes[col]
        state.mutes[col] = new_mute
        state.osc.send("/live/track/set/mute", [ids[col], 1 if new_mute else 0])
        print(f"[mute] track {ids[col] + 1} -> {'OFF' if new_mute else 'ON'}")
        strip.mark(col + 1)

    def raw_callback(device, data):
        try:
            if len(data) < 11 or bytes(data[0:7]) != HEADER:
                return
            code, val = data[9], data[10]
            if code in KEY_CODES:
                on_key(KEY_CODES[code], val == 0x01)
            elif code in SWIPE_CODES:
                on_swipe(SWIPE_CODES[code])
            elif code in TAP_ZONE_CC:
                on_tap(code)
            elif code in KNOB_ROTATE_CODES:
                on_knob_rotate(*KNOB_ROTATE_CODES[code])
            elif code in KNOB_PUSH_CODES and val == 0x01:
                on_knob_push(KNOB_PUSH_CODES[code])
        except Exception as e:
            import traceback
            print(f"[handler error] {e}")
            traceback.print_exc()

    return (raw_callback, refresh_all, on_track_selected, on_scene_selected,
            on_volume_changed, on_meter_left_changed, on_meter_right_changed,
            on_playing_slot_changed, on_fired_slot_changed, stop_all_listeners)


def main():
    time.sleep(0.3)
    osc = OSCBridge()

    reply = osc.query("/live/song/get/tempo", timeout=1.0)
    if not reply:
        print("Could not reach AbletonOSC. Is Live open with AbletonOSC "
              "selected as a Control Surface?")
        return
    print(f"Connected to Ableton Live (tempo {reply[0]} bpm)")

    manager = DeviceManager()
    devices = manager.enumerate()
    if not devices:
        print("No Stream Dock device found.")
        return

    device = devices[0]
    device.open()
    device.init()
    print(f"Connected to {type(device).__name__}, firmware {device.firmware_version}")

    state = State(osc)
    io = DeviceIO(device)
    strip = StripPainter(io, state)
    keys = KeyPainter(io, state)
    midi = MidiBridge()

    (handler, refresh_all, on_track_selected, on_scene_selected,
     on_volume_changed, on_meter_left_changed, on_meter_right_changed,
     on_playing_slot_changed, on_fired_slot_changed,
     stop_all_listeners) = make_handler(state, io, strip, keys, midi)
    device.set_raw_read_callback(handler, async_run=False)

    # SIGTERM (plain `kill`, as opposed to Ctrl+C's SIGINT) does not run
    # Python's finally blocks by default -- convert it to a KeyboardInterrupt
    # so shutdown always reaches the cleanup below and unsubscribes properly.
    def handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)

    osc.add_listener("/live/view/get/selected_track", on_track_selected)
    osc.send("/live/view/start_listen/selected_track", [])
    osc.add_listener("/live/view/get/selected_scene", on_scene_selected)
    osc.send("/live/view/start_listen/selected_scene", [])
    osc.add_listener("/live/track/get/volume", on_volume_changed)
    osc.add_listener("/live/track/get/output_meter_left", on_meter_left_changed)
    osc.add_listener("/live/track/get/output_meter_right", on_meter_right_changed)
    osc.add_listener("/live/track/get/playing_slot_index", on_playing_slot_changed)
    osc.add_listener("/live/track/get/fired_slot_index", on_fired_slot_changed)

    try:
        refresh_all()
    except Exception as e:
        import traceback
        print(f"[startup refresh error] {e}")
        traceback.print_exc()

    print(f"\nTracks: {state.num_tracks}   Scenes: {state.num_scenes}   "
          "(re-read live, no restart needed)")
    print("\nRunning. Ctrl+C to stop.")
    print("Keys 1-4 / 6-9 = fire clip | Keys 5 / 10 = launch that row's scene")
    print(f"Swipe strip = jump {SWIPE_TRACKS} tracks (left-to-right = forward)")
    zones = " / ".join(str(TAP_ZONE_CC[c]) for c in sorted(TAP_ZONE_CC))
    print(f"Tap zones 1-4 (left to right) = CC {zones}")
    print(f"Knob 3 push = CC {KNOB_PUSH_CC[3]} | Knob 4 push = CC {KNOB_PUSH_CC[4]}")
    print("Knob 1/2 = volume, push = mute | Knob 3 = track, Knob 4 = scene")
    print(f"Knob 3 push = CC {KNOB_PUSH_CC[3]} | Knob 4 push = CC {KNOB_PUSH_CC[4]}"
          f"  (MIDI channel {MIDI_CHANNEL + 1}, port '{MIDI_PORT_NAME}')\n")

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all_listeners()
        midi.close()
        device.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()
