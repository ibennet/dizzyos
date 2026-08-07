"""System overlays — status the kernel draws over whatever app is on screen.

Apps render their frames as usual; the launcher passes each finished frame
(including mid-transition composites) through `OverlayManager.compose`, so a
status overlay is visible no matter which app has focus. Two ship today: the
red no-WiFi icon (top-left, while the network is down) and the setup PIN
banner (30s, when someone opens the LAN settings page).

Overlays are registered from background threads (network monitor, settings
server) and composed on the render loop, hence the lock. Draw functions are
plain `fn(frame)` callables that paint onto the PIL frame in place.
"""

import threading
import time

from PIL import ImageDraw

# Icon palette. The arcs are kept dim so the bright slash carries the "off".
_ARC = (200, 45, 40)
_SLASH = (255, 70, 60)
_BACKDROP = (0, 0, 0)

_BANNER_BG = (10, 10, 12)
_BANNER_BORDER = (200, 60, 50)
_BANNER_LABEL = (150, 150, 155)
_BANNER_PIN = (255, 255, 255)

#: Pixel plots for the WiFi glyph (x, y), an 11x9 box: two arcs and the dot.
#: Hand-plotted — Pillow's arc() staircases into mush at this size. 11x9 rather
#: than something tighter because the slash below has to eat pixels out of the
#: glyph to stay separable, and a smaller fan has none to spare.
_WIFI_ARCS = [
    (3, 0), (4, 0), (5, 0), (6, 0), (7, 0),           # outer arc, crown
    (1, 1), (2, 1), (8, 1), (9, 1),                   # outer arc, shoulders
    (0, 2), (10, 2),                                  # outer arc, tips
    (3, 4), (4, 4), (5, 4), (6, 4), (7, 4),           # inner arc, crown
    (2, 5), (8, 5),                                   # inner arc, tips
    (4, 7), (5, 7), (6, 7), (4, 8), (5, 8), (6, 8),   # the dot
]

#: The "no" slash: a true 45° diagonal across the glyph. It is drawn 1px wide
#: with a black pixel carved out either side — at this size a fatter slash just
#: erases the arcs and the whole thing reads as a red squiggle, whereas the
#: black channel keeps slash and arcs separable.
_WIFI_SLASH = [(i + 1, i) for i in range(9)]


class OverlayManager:
    """Named overlay layers with optional TTLs, composed newest-last."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._layers = {}  # name -> (draw_fn, expires_at | None)

    def show(self, name, draw_fn, ttl=None):
        """Add/replace overlay `name`. With `ttl` (seconds) it self-clears."""
        expires = self._clock() + ttl if ttl else None
        with self._lock:
            self._layers[name] = (draw_fn, expires)

    def hide(self, name):
        with self._lock:
            self._layers.pop(name, None)

    def active(self):
        """Names currently showing (expired layers dropped first)."""
        now = self._clock()
        with self._lock:
            self._layers = {n: (fn, exp) for n, (fn, exp) in self._layers.items()
                            if exp is None or exp > now}
            return list(self._layers)

    def compose(self, frame):
        """Draw all active overlays onto `frame` (in place) and return it."""
        now = self._clock()
        with self._lock:
            live = [(n, fn) for n, (fn, exp) in self._layers.items()
                    if exp is None or exp > now]
            self._layers = {n: self._layers[n] for n, _ in live}
        for _, fn in live:
            fn(frame)
        return frame


def no_wifi_icon(frame):
    """The red wifi-off glyph, top-left, on a black backdrop tile so it stays
    legible over any app content."""
    draw = ImageDraw.Draw(frame)
    ox, oy = 2, 2  # icon origin inside the backdrop tile
    draw.rectangle((0, 0, ox + 11, oy + 9), fill=_BACKDROP)
    for x, y in _WIFI_ARCS:
        draw.point((ox + x, oy + y), fill=_ARC)
    for x, y in _WIFI_SLASH:  # carve the channel, then lay the slash in it
        draw.point((ox + x - 1, oy + y), fill=_BACKDROP)
        draw.point((ox + x + 1, oy + y), fill=_BACKDROP)
    for x, y in _WIFI_SLASH:
        draw.point((ox + x, oy + y), fill=_SLASH)


def pin_banner(fonts, pin):
    """Build a draw function showing `pin` centered on the sign — the pairing
    proof for the LAN settings page (physical presence: you can only read it
    off the sign itself)."""
    from .pixelfont import GLYPH_H

    font = fonts.pixel()

    def draw_fn(frame):
        draw = ImageDraw.Draw(frame)
        label = "SETUP PIN"
        label_w, label_h = font.measure(label), GLYPH_H
        pin_w, pin_h = font.measure(pin, scale=2), GLYPH_H * 2
        pad = 4
        box_w = max(label_w, pin_w) + pad * 2
        box_h = label_h + 3 + pin_h + pad * 2
        x0 = (frame.width - box_w) // 2
        y0 = (frame.height - box_h) // 2
        draw.rectangle((x0, y0, x0 + box_w - 1, y0 + box_h - 1), fill=_BANNER_BG)
        draw.rectangle((x0, y0, x0 + box_w - 1, y0 + box_h - 1), outline=_BANNER_BORDER)
        font.draw_text(draw, x0 + (box_w - label_w) // 2, y0 + pad,
                       label, _BANNER_LABEL)
        font.draw_text(draw, x0 + (box_w - pin_w) // 2, y0 + pad + label_h + 3,
                       pin, _BANNER_PIN, scale=2)

    return draw_fn
