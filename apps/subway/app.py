"""Subway app — next trains at a handful of nearby stations.

Live arrivals straight from the MTA's GTFS-realtime feeds (keyless, no middleman).
The feeds are protobuf, decoded by the kernel's small shared reader — see
`kernel/gtfs.py`.

One column per stop, each split into a southbound (↓) and northbound (↑) block,
showing the next couple of trains as an MTA line bullet in the line's official color
plus minutes away. Arrival times are kept as absolute timestamps, so the countdown
keeps ticking between the app's data refreshes.
"""

import time

from PIL import ImageDraw

from kernel.app import App

from kernel.gtfs import iter_arrivals, looks_like_feed

# The MTA splits realtime data across feeds by trunk line; only the feeds the
# configured stops actually need get fetched.
FEEDS = {
    "gtfs-ace": ("A", "C", "E", "H", "FS"),
    "gtfs-bdfm": ("B", "D", "F", "M"),
    "gtfs-nqrw": ("N", "Q", "R", "W"),
    "gtfs-g": ("G",),
    "gtfs-jz": ("J", "Z"),
    "gtfs-l": ("L",),
    "gtfs": ("1", "2", "3", "4", "5", "6", "7", "GS"),  # the numbered (IRT) lines
}

# Official MTA line colors, by trunk.
LINE_COLORS = {
    "A": "#0039A6", "C": "#0039A6", "E": "#0039A6",
    "B": "#FF6319", "D": "#FF6319", "F": "#FF6319", "M": "#FF6319",
    "G": "#6CBE45",
    "J": "#996633", "Z": "#996633",
    "L": "#A7A9AC",
    "N": "#FCCC0A", "Q": "#FCCC0A", "R": "#FCCC0A", "W": "#FCCC0A",
    "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
    "4": "#00933C", "5": "#00933C", "6": "#00933C",
    "7": "#B933AD",
}
UNKNOWN_LINE = "#666666"
# The yellow and grey bullets need dark letters to stay legible.
DARK_LETTER = frozenset("NQRWL")

PALETTE = {
    "stop": (235, 235, 235),   # column header — the station name
    "mins": (245, 245, 245),   # minutes away
    "soon": (255, 196, 84),    # amber for a train that's here now
    "dim": (110, 120, 130),    # direction arrows, and "no trains" dashes
}

# Direction is carried by a filled strip down the left gutter — ice blue for downtown,
# warm white for uptown — rather than by a wash behind the whole block. A background
# wash competes with the line bullets for attention; the gutter never overlaps them.
# Both hues are deliberately ones no MTA line uses, so a solid bar can only read as a
# direction marker and never as line semantics (orange, for instance, means B/D/F/M).
GUTTER_FILL = {"S": (120, 190, 225), "N": (240, 225, 200)}
# The arrow is knocked out of that strip in black (unlit pixels), which reads better
# at 5x7 than drawing it on top — see ARROW_GLYPH for why it's a solid shape.
ARROW_COLOR = (0, 0, 0)
# Full-width wash behind each block: a darker grey downtown, a lighter one uptown.
# Neither is pure black, so the panel has a consistent floor and the header/downtown/
# uptown read as a three-step ramp rather than an on/off cliff. The blue channel runs
# a hair higher to stop the greys reading as a warm cast next to the orange gutter.
# These need to be brighter than they look to register at all: the panel scales them
# to `matrix.brightness` (70%) and lights them as discrete LEDs with dark gaps
# between, so much under ~20 disappears entirely.
BAND = {"S": (22, 22, 23), "N": (48, 48, 50)}
# The header sits on its own accent band. Dark lavender: cool enough to stay distinct
# against both halves of the gutter, and dark enough for white text to read on it.
HEADER_BAND = (78, 60, 145)

# Solid-headed arrows, 5x7 to match the font cell. The font's stroked ↑/↓ break into
# disconnected specks when knocked out of a colored fill; a filled head survives it.
ARROW_GLYPH = {
    "S": ("..#..", "..#..", "..#..", "#####", ".###.", "..#..", "....."),
    "N": (".....", "..#..", ".###.", "#####", "..#..", "..#..", "..#.."),
}

GLYPH_W, GLYPH_H = 5, 7  # the pixel font's cell, at scale 1
# A bullet has to be wide enough that the glyph's corners stay inside the disc —
# at 9px the round edge clips the letters.
BULLET = 11
# The filled direction strip: 7px wide so the 5px arrow has 1px of fill either side.
GUTTER = 7
# One blank column between the gutter and the first bullet, so the strip never
# touches the line bullets.
CONTENT_X = GUTTER + 1
ROW_GAP = 2       # vertical gap between the two trains in a direction block
ROW_H = BULLET + ROW_GAP
BLOCK_H = 2 * ROW_H - ROW_GAP  # a direction block is two train rows tall
HEADER_Y = 0
HEADER_H = 8       # the stop-name band across the top
MAX_TRAINS = 2     # trains shown per direction when there is room for both
DESIGN_H = 64      # the canvas this app's layout was drawn for


def _layout(height):
    """Geometry for a canvas `height` px tall.

    Derived rather than hard-coded because the canvas is not always 64 rows:
    a 64-row panel driven without the address-E jumper reports 32, and this
    app used to raise `y1 must be >= y0` on every frame there — taking the
    whole sign down with it. Below the two-train threshold each direction
    shows its next train only.

    Returns first/last row pairs (inclusive) that tile the canvas with no
    black seam, plus the top row of each direction block.
    """
    if height == DESIGN_H:
        # The design size is hand-tuned (the bands are deliberately uneven so
        # the blocks sit where they look right); don't let the derivation
        # shift it by a pixel.
        return {
            "header_rows": (0, 7), "header_h": HEADER_H,
            "bands": {"S": (8, 34), "N": (35, DESIGN_H - 1)},
            "block_y": {"S": 10, "N": 37},
            "block_h": MAX_TRAINS * ROW_H - ROW_GAP,
            "trains": MAX_TRAINS,
            "body_top": 10, "body_h": 37 + (MAX_TRAINS * ROW_H - ROW_GAP) - 10,
        }

    header_h = HEADER_H if height >= 24 else 0
    body_h = height - header_h
    trains = MAX_TRAINS if body_h >= 2 * (MAX_TRAINS * ROW_H - ROW_GAP) else 1
    block_h = trains * ROW_H - ROW_GAP

    half = body_h // 2                    # rows given to the southbound band
    bands = {"S": (header_h, header_h + half - 1),
             "N": (header_h + half, height - 1)}
    # Center each block in its band; clamp so a very short canvas still draws.
    block_y = {d: max(first, first + ((last - first + 1) - block_h) // 2)
               for d, (first, last) in bands.items()}
    return {
        "header_rows": (0, max(header_h - 1, 0)),
        "header_h": header_h,
        "bands": bands,
        "block_y": block_y,
        "block_h": block_h,
        "trains": trains,
        "body_top": header_h,
        "body_h": body_h,
    }


class SubwayApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._arrivals = {}   # stop id -> {"N": [(route, epoch)], "S": [...]}
        self._ok_feeds = set()

    # --- data --------------------------------------------------------------
    def refresh(self):
        stops = self._stops()
        wanted = {route for stop in stops for route in stop["routes"]}
        arrivals = {stop["id"]: {"N": [], "S": []} for stop in stops}
        by_stop = {stop["id"]: stop for stop in stops}
        ok_feeds = set()

        for key, routes in FEEDS.items():
            if not wanted.intersection(routes):
                continue
            if self._load_feed(key, arrivals, by_stop):
                ok_feeds.add(key)

        for buckets in arrivals.values():
            for trains in buckets.values():
                trains.sort(key=lambda train: train[1])

        # Rebind whole, so a render mid-refresh sees the old data, never a partial mix.
        self._arrivals, self._ok_feeds = arrivals, ok_feeds

    def _load_feed(self, key, arrivals, by_stop):
        """Fetch one feed and fold its arrivals into `arrivals`. Returns True if the
        feed produced usable data — one failing feed must not blank the others."""
        # The base already ends in its separator (the MTA's is a URL-encoded "%2F"),
        # so the feed key is appended directly.
        base = self.config.get("feed_base", "")
        ttl = self.config.get("feed_ttl", 30)
        try:
            raw = self.services.data.get_bytes(f"{base}{key}", ttl=ttl)
        except Exception as exc:  # noqa: BLE001 - a dead feed is a display state
            self.services.log(f"subway: {key} unavailable: {exc}")
            return False
        if not looks_like_feed(raw):
            self.services.log(f"subway: {key} did not return a GTFS-realtime feed")
            return False

        for route, stop_id, when in iter_arrivals(raw):
            # Child stop ids are the parent plus a direction suffix: F11N / F11S.
            parent, direction = stop_id[:-1], stop_id[-1:]
            stop = by_stop.get(parent)
            if stop is None or direction not in ("N", "S"):
                continue
            if route in stop["routes"]:
                arrivals[parent][direction].append((route, when))
        return True

    def _stops(self):
        """The configured stops, skipping any malformed entry rather than crashing."""
        stops = []
        for entry in self.config.get("stops") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            routes = [str(r) for r in (entry.get("routes") or [])]
            stops.append(
                {"id": str(entry["id"]), "name": str(entry.get("name", entry["id"])), "routes": routes}
            )
        return stops

    def _has_feed(self, stop):
        """A stop is only "no feed" if every feed it depends on failed."""
        return any(
            key in self._ok_feeds and not set(routes).isdisjoint(stop["routes"])
            for key, routes in FEEDS.items()
        )

    def _next_trains(self, stop, direction, now):
        """Up to `per_direction` upcoming trains as (route, minutes), soonest first.

        `min_minutes` hides trains too soon to be worth walking for; `max_minutes`
        hides ones too far out to be useful.
        """
        min_mins = self.config.get("min_minutes", 0)
        max_mins = self.config.get("max_minutes", 60)
        upcoming = []
        for route, when in self._arrivals.get(stop["id"], {}).get(direction, ()):
            mins = int((when - now) // 60)
            if min_mins <= mins <= max_mins:
                upcoming.append((route, mins))
            if len(upcoming) >= self.config.get("per_direction", 2):
                break  # the buckets are time-sorted, so the rest are further out
        return upcoming

    def _header_color(self):
        """Accent color for the station names. `header_color` overrides the default."""
        return _rgb(self.config.get("header_color"), PALETTE["stop"])

    def _band(self, direction):
        """Optional full-width wash behind a direction block; black (off) by default.
        `band_downtown` / `band_uptown` override it."""
        key = "band_downtown" if direction == "S" else "band_uptown"
        return _rgb(self.config.get(key), BAND[direction])

    def _gutter(self, direction):
        """Fill color of the direction strip. `gutter_downtown` / `gutter_uptown`
        override the defaults."""
        key = "gutter_downtown" if direction == "S" else "gutter_uptown"
        return _rgb(self.config.get(key), GUTTER_FILL[direction])

    # --- rendering ---------------------------------------------------------
    def render(self, t):
        # Data is primed by the launcher's off-loop refresh() before the first frame;
        # minutes are derived here so the countdown ticks between refreshes.
        stops = self._stops()
        image = self.blank()
        draw = ImageDraw.Draw(image)
        if not stops:
            return image

        pf = self.services.fonts.pixel()
        now = time.time()
        column_w = (self.services.width - CONTENT_X) // len(stops)
        lay = _layout(self.services.height)

        for direction in ("S", "N"):
            first, last = lay["bands"][direction]
            band = self._band(direction)
            if band != (0, 0, 0):  # optional wash behind the whole block
                draw.rectangle([0, first, self.services.width - 1, last], fill=band)
            # The gutter strip carries the direction, arrow knocked out of it.
            draw.rectangle([0, first, GUTTER - 1, last], fill=self._gutter(direction))
            arrow_y = lay["block_y"][direction] + (lay["block_h"] - GLYPH_H) // 2
            _blit(draw, 1, arrow_y, ARROW_GLYPH[direction], ARROW_COLOR)

        # Header band, flush against the downtown band below it.
        if lay["header_h"]:
            draw.rectangle(
                [0, lay["header_rows"][0], self.services.width - 1, lay["header_rows"][1]],
                fill=_rgb(self.config.get("header_band"), HEADER_BAND),
            )

        header_color = self._header_color()
        for index, stop in enumerate(stops):
            x = CONTENT_X + index * column_w
            name = _ellipsize(pf, stop["name"], column_w - 2)
            pf.draw_text(draw, x, HEADER_Y, name, header_color)
            if not self._has_feed(stop):
                # This stop's feeds all failed — say so once for the column, rather
                # than showing it as "no trains" in either direction.
                _draw_centered(draw, pf, x, ("no", "feed"), PALETTE["dim"], lay=lay)
                continue
            for direction in ("S", "N"):
                self._draw_block(draw, pf, x, lay["block_y"][direction], stop,
                                 direction, now, lay["trains"])

        return image

    def _draw_block(self, draw, pf, x, top, stop, direction, now, rows=MAX_TRAINS):
        """One direction's train rows for one stop (`rows` of them if there is room)."""
        text_y = top + (BULLET - GLYPH_H) // 2  # line text up with the bullets
        trains = self._next_trains(stop, direction, now)
        if not trains:
            pf.draw_text(draw, x, text_y, "-", PALETTE["dim"])
            return
        for row, (route, mins) in enumerate(trains[:rows]):
            self._draw_train(draw, pf, x, top + row * ROW_H, route, mins)

    @staticmethod
    def _draw_train(draw, pf, x, y, route, mins):
        """An MTA line bullet plus the minutes away."""
        draw.ellipse([x, y, x + BULLET - 1, y + BULLET - 1], fill=LINE_COLORS.get(route, UNKNOWN_LINE))
        letter = (0, 0, 0) if route in DARK_LETTER else (255, 255, 255)
        pf.draw_text(draw, x + (BULLET - GLYPH_W) // 2, y + (BULLET - GLYPH_H) // 2, route, letter)
        text = "now" if mins <= 0 else str(mins)
        color = PALETTE["soon"] if mins <= 0 else PALETTE["mins"]
        pf.draw_text(draw, x + BULLET + 2, y + (BULLET - GLYPH_H) // 2, text, color)


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
