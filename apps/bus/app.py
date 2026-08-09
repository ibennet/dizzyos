"""Bus app — next buses at a handful of nearby stops.

Live arrivals from the MTA Bus Time GTFS-realtime feed (one citywide protobuf,
decoded by the kernel's shared reader — see `kernel/gtfs.py`, which the subway
app uses too).

The layout mirrors the subway app — one column per configured stop group, each
split into two direction blocks with a filled chip and a knocked-out arrow —
with two bus-shaped differences:

* Bus stops carry no direction suffix: each curbside stop is its own id, one
  direction only. So a block is bound to explicit stop ids (plural — a local
  stop and its SBS box a few feet apart merge into one block) rather than to a
  parent station plus N/S.
* Route names ("M15", "M15-SBS") don't fit the subway's 11px disc, so routes
  render as a rounded pill wide enough for the label. With `short_labels` the
  borough prefix is dropped ("M15+" becomes "15+") — every nearby route shares
  the borough letter, so it carries no information the rider needs. SBS routes
  (the feed spells them with a trailing "+") get the Select Bus Service teal;
  everything else the MTA bus blue.

Arrival times are absolute timestamps, so the countdown keeps ticking between
data refreshes.
"""

import time

from PIL import ImageDraw

from kernel.app import App

from kernel.gtfs import iter_arrivals, looks_like_feed

# MTA bus branding: the NYCT bus blue for locals, the Select Bus Service teal
# for SBS routes (route ids ending in "+"). Both take white text.
LOCAL_FILL = (0, 57, 166)
SBS_FILL = (0, 129, 145)

PALETTE = {
    "stop": (235, 235, 235),   # column header — the stop-group name
    "mins": (245, 245, 245),   # minutes away
    "soon": (255, 196, 84),    # amber for a bus that's here now
    "dim": (110, 120, 130),    # "no buses" dashes
}

# Direction is carried by a filled chip with the arrow knocked out in black —
# the subway app's gutter scheme, with the same two hues (ice blue for a
# column's first block, warm white for its second) so the sign reads as one
# family. But where the subway's strip spans the panel (every column there is
# downtown-over-uptown), here each column carries its own chip: an avenue
# column reads S/N while a crosstown column reads E/W, so one shared strip
# would lie about somebody.
CHIP_FILL = ((120, 190, 225), (240, 225, 200))
ARROW_COLOR = (0, 0, 0)
# Full-width wash behind each block — darker first, lighter second, neither pure
# black, matching the subway app (see its manifest for why values under ~20
# vanish on the panel).
BAND = ((22, 22, 23), (48, 48, 50))
# The header band. Deep teal-green: distinct from the subway app's lavender at a
# glance (so you know which app you're looking at mid-rotation), cool enough to
# sit against both gutter hues, dark enough for white text.
HEADER_BAND = (20, 95, 80)

# Solid-headed arrows, 5x7 to match the font cell; stroked glyphs break into
# specks when knocked out of a fill. N/S as in the subway app, E/W for the
# crosstown blocks.
ARROW_GLYPH = {
    "S": ("..#..", "..#..", "..#..", "#####", ".###.", "..#..", "....."),
    "N": (".....", "..#..", ".###.", "#####", "..#..", "..#..", "..#.."),
    "W": (".....", "..#..", ".##..", "#####", ".##..", "..#..", "....."),
    "E": (".....", "..#..", "..##.", "#####", "..##.", "..#..", "....."),
}

GLYPH_W, GLYPH_H = 5, 7  # the pixel font's cell, at scale 1
PILL_H = 11        # pill height — matches the subway bullet so rows line up
PILL_PAD = 3       # fill either side of the route text inside the pill
CHIP = 7           # the direction chip, wide enough for a 5px arrow
ROW_X = CHIP + 2   # pills start here; the chip never touches them
ROW_GAP = 2        # vertical gap between the two buses in a direction block
ROW_H = PILL_H + ROW_GAP
HEADER_Y = 0
HEADER_H = 8       # the stop-name band across the top
MAX_BUSES = 2      # buses shown per direction when there is room for both
DESIGN_H = 64      # the canvas this app's layout was drawn for


def _layout(height):
    """Geometry for a canvas `height` px tall — same derivation as the subway
    app's, for the same reason: a mis-jumpered panel reports 32 rows, and the
    layout has to tile that without raising. Below the two-bus threshold each
    direction shows its next bus only."""
    if height == DESIGN_H:
        return {
            "header_rows": (0, 7), "header_h": HEADER_H,
            "bands": ((8, 34), (35, DESIGN_H - 1)),
            "block_y": (10, 37),
            "block_h": MAX_BUSES * ROW_H - ROW_GAP,
            "buses": MAX_BUSES,
            "body_top": 10, "body_h": 37 + (MAX_BUSES * ROW_H - ROW_GAP) - 10,
        }

    header_h = HEADER_H if height >= 24 else 0
    body_h = height - header_h
    buses = MAX_BUSES if body_h >= 2 * (MAX_BUSES * ROW_H - ROW_GAP) else 1
    block_h = buses * ROW_H - ROW_GAP

    half = body_h // 2
    bands = ((header_h, header_h + half - 1), (header_h + half, height - 1))
    block_y = tuple(max(first, first + ((last - first + 1) - block_h) // 2)
                    for first, last in bands)
    return {
        "header_rows": (0, max(header_h - 1, 0)),
        "header_h": header_h,
        "bands": bands,
        "block_y": block_y,
        "block_h": block_h,
        "buses": buses,
        "body_top": header_h,
        "body_h": body_h,
    }


class BusApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._arrivals = {}   # stop id -> [(route, epoch)], time-sorted
        self._feed_ok = False

    # --- data --------------------------------------------------------------
    def refresh(self):
        stops = self._stops()
        wanted = {sid for stop in stops for block in stop["blocks"] for sid in block["ids"]}
        arrivals = {sid: [] for sid in wanted}

        url = self.config.get("feed_url", "")
        if self.config.get("api_key"):
            url += ("&" if "?" in url else "?") + "key=" + self.config["api_key"]
        try:
            raw = self.services.data.get_bytes(url, ttl=self.config.get("feed_ttl", 30))
        except Exception as exc:  # noqa: BLE001 - a dead feed is a display state
            self.services.log(f"bus: feed unavailable: {exc}")
            self._feed_ok = False
            return
        if not looks_like_feed(raw):
            self.services.log("bus: feed did not return a GTFS-realtime feed")
            self._feed_ok = False
            return

        for route, stop_id, when in iter_arrivals(raw):
            if stop_id in arrivals:
                arrivals[stop_id].append((route, when))
        for buses in arrivals.values():
            buses.sort(key=lambda bus: bus[1])

        # Rebind whole, so a render mid-refresh sees the old data, never a partial mix.
        self._arrivals, self._feed_ok = arrivals, True

    def _stops(self):
        """The configured stop groups, skipping any malformed entry rather than
        crashing. Each group is one column: a name plus up to two direction
        blocks of `{dir, ids, routes}`."""
        stops = []
        for entry in self.config.get("stops") or []:
            if not isinstance(entry, dict):
                continue
            blocks = []
            for block in (entry.get("blocks") or [])[:2]:
                if not isinstance(block, dict) or not block.get("ids"):
                    continue
                blocks.append({
                    "dir": str(block.get("dir", "N")),
                    "ids": [str(i) for i in block["ids"]],
                    "routes": [str(r) for r in (block.get("routes") or [])],
                })
            if blocks:
                stops.append({"name": str(entry.get("name", "")), "blocks": blocks})
        return stops

    def _next_buses(self, block, now):
        """Upcoming buses for one direction block as (route, minutes), soonest
        first, the block's stop ids merged. `min_minutes` hides buses too soon
        to walk for; `max_minutes` hides ones too far out to be useful."""
        min_mins = self.config.get("min_minutes", 0)
        max_mins = self.config.get("max_minutes", 30)
        per = self.config.get("per_direction", 2)
        merged = sorted(
            (when, route)
            for sid in block["ids"]
            for route, when in self._arrivals.get(sid, ())
            if not block["routes"] or route in block["routes"]
        )
        upcoming = []
        for when, route in merged:
            mins = int((when - now) // 60)
            if min_mins <= mins <= max_mins:
                upcoming.append((route, mins))
            if len(upcoming) >= per:
                break
        return upcoming

    def _label(self, route):
        """The text inside a route pill. With `short_labels`, the borough prefix
        goes ("M15+" -> "15+") — it's the same letter on every nearby route."""
        if self.config.get("short_labels", True):
            return route.lstrip("ABCMQSX") or route
        return route

    def _pill_fill(self, route):
        return SBS_FILL if route.endswith("+") else LOCAL_FILL

    def _chip(self, index):
        """Fill color of a column's first/second direction chip.
        `chip_first` / `chip_second` override the defaults."""
        key = ("chip_first", "chip_second")[index]
        return _rgb(self.config.get(key), CHIP_FILL[index])

    def _band(self, index):
        """Full-width wash behind a column's first/second block.
        `band_first` / `band_second` override; [0, 0, 0] turns it off."""
        key = ("band_first", "band_second")[index]
        return _rgb(self.config.get(key), BAND[index])

    # --- rendering ---------------------------------------------------------
    def render(self, t):
        stops = self._stops()
        image = self.blank()
        draw = ImageDraw.Draw(image)
        if not stops:
            return image

        pf = self.services.fonts.pixel()
        now = time.time()
        column_w = self.services.width // len(stops)
        lay = _layout(self.services.height)

        for index in (0, 1):
            first, last = lay["bands"][index]
            band = self._band(index)
            if band != (0, 0, 0):
                draw.rectangle([0, first, self.services.width - 1, last], fill=band)

        if lay["header_h"]:
            draw.rectangle(
                [0, lay["header_rows"][0], self.services.width - 1, lay["header_rows"][1]],
                fill=_rgb(self.config.get("header_band"), HEADER_BAND),
            )

        header_color = _rgb(self.config.get("header_color"), PALETTE["stop"])
        for col, stop in enumerate(stops):
            x = col * column_w
            name = _ellipsize(pf, stop["name"], column_w - 2)
            pf.draw_text(draw, x + 1, HEADER_Y, name, header_color)
            # Every column shares the one feed, so "no feed" is a whole-panel
            # state — but keep the message per column so the layout holds.
            if not self._feed_ok:
                _draw_centered(draw, pf, x + ROW_X, ("no", "feed"), PALETTE["dim"], lay=lay)
                continue
            # A column's pills are all one width (the widest of its routes), so
            # the minutes line up down the column.
            pill_w = max(
                (pf.measure(self._label(r)) + 2 * PILL_PAD
                 for block in stop["blocks"] for r in block["routes"]),
                default=PILL_H,
            )
            for index, block in enumerate(stop["blocks"]):
                top = lay["block_y"][index]
                # The direction chip: block-tall, arrow knocked out mid-height.
                draw.rectangle([x, top, x + CHIP - 1, top + lay["block_h"] - 1],
                               fill=self._chip(index))
                arrow_y = top + (lay["block_h"] - GLYPH_H) // 2
                _blit(draw, x + 1, arrow_y,
                      ARROW_GLYPH.get(block["dir"], ARROW_GLYPH["N"]), ARROW_COLOR)
                self._draw_block(draw, pf, x + ROW_X, top, block, pill_w,
                                 now, lay["buses"])

        return image

    def _draw_block(self, draw, pf, x, top, block, pill_w, now, rows=MAX_BUSES):
        """One direction's bus rows for one column (`rows` of them if there is room)."""
        text_y = top + (PILL_H - GLYPH_H) // 2
        buses = self._next_buses(block, now)
        if not buses:
            pf.draw_text(draw, x, text_y, "-", PALETTE["dim"])
            return
        for row, (route, mins) in enumerate(buses[:rows]):
            self._draw_bus(draw, pf, x, top + row * ROW_H, route, mins, pill_w)

    def _draw_bus(self, draw, pf, x, y, route, mins, pill_w):
        """A route pill plus the minutes away."""
        draw.rounded_rectangle([x, y, x + pill_w - 1, y + PILL_H - 1],
                               radius=PILL_H // 2, fill=self._pill_fill(route))
        label = self._label(route)
        pf.draw_text(draw, x + (pill_w - pf.measure(label)) // 2,
                     y + (PILL_H - GLYPH_H) // 2, label, (255, 255, 255))
        text = "now" if mins <= 0 else str(mins)
        color = PALETTE["soon"] if mins <= 0 else PALETTE["mins"]
        pf.draw_text(draw, x + pill_w + 2, y + (PILL_H - GLYPH_H) // 2, text, color)


def _blit(draw, x, y, rows, color):
    """Draw a '#'-per-lit-pixel bitmap with its top-left at (x, y)."""
    for row_y, row in enumerate(rows):
        for row_x, cell in enumerate(row):
            if cell == "#":
                draw.point((x + row_x, y + row_y), fill=color)


def _rgb(value, default):
    """Coerce a configured [R, G, B] to a color tuple, falling back to `default`."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in value)
        except (TypeError, ValueError):
            pass
    return default


def _draw_centered(draw, font, x, lines, color, gap=3, lay=None):
    """Stack `lines` vertically, centered in the body area below the header."""
    lay = lay or _layout(64)
    top = lay["body_top"] + (lay["body_h"] - (len(lines) * (GLYPH_H + gap) - gap)) // 2
    for row, text in enumerate(lines):
        font.draw_text(draw, x, top + row * (GLYPH_H + gap), text, color)


def _ellipsize(font, text, max_w):
    """Trim `text` (appending an ellipsis) until it fits within `max_w` pixels."""
    if not text or font.measure(text) <= max_w:
        return text
    while text and font.measure(text + "…") > max_w:
        text = text[:-1]
    return text + "…" if text else ""
