"""Dwell-progress indicator — kernel chrome showing how far the current app is
through its slot before the launcher rotates to the next screen.

Drawn by the launcher on every composed frame rather than through
OverlayManager: overlay sprites are rasterised once and cached until show()
is called again, which is the wrong shape for something that changes every
frame. Configured under `launcher.progress` in config.yaml; when disabled
(or during transitions / fallback frames, where "progress to the next
screen" is meaningless) draw() is a no-op.

Styles:
  bar    a thin strip flush along the top or bottom edge, filling across
  dots   one dot per app in the rotation, page-indicator style; the current
         app's dot brightens as its dwell elapses
  arc    a tiny clock-style ring in the bottom-right corner, lit clockwise
"""

STYLES = ("bar", "dots", "arc")

#: Perimeter of a 7x7 ring, ordered clockwise from top-center — the sweep
#: order for the `arc` style. Hand-plotted like overlay.py's wifi glyph:
#: Pillow's arc() staircases into mush at this size.
_ARC_RING = [
    (3, 0), (4, 0),
    (5, 1), (6, 2), (6, 3), (6, 4), (5, 5),
    (4, 6), (3, 6), (2, 6),
    (1, 5), (0, 4), (0, 3), (0, 2), (1, 1),
    (2, 0),
]
_ARC_MARGIN = 2  # px between the ring's backdrop tile and the frame edge


def _lerp(a, b, t):
    """Channel-wise blend of two RGB tuples at t in 0..1."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


class ProgressIndicator:
    def __init__(self, cfg, log=None):
        cfg = cfg or {}
        log = log or (lambda msg: None)
        self.enabled = bool(cfg.get("enabled", False))

        self.style = cfg.get("style", "bar")
        if self.style not in STYLES:
            log(f"progress: unknown style {self.style!r}, using bar")
            self.style = "bar"

        self.position = cfg.get("position", "bottom")
        if self.position not in ("bottom", "top"):
            log(f"progress: unknown position {self.position!r}, using bottom")
            self.position = "bottom"

        self.direction = cfg.get("direction", "fill")
        if self.direction not in ("fill", "drain"):
            log(f"progress: unknown direction {self.direction!r}, using fill")
            self.direction = "fill"

        self.thickness = max(1, min(int(cfg.get("thickness", 1)), 4))
        self.color = tuple(cfg.get("color", (90, 90, 110)))[:3]
        self.track = tuple(cfg.get("track", (18, 18, 24)))[:3]
        self.show_after = max(float(cfg.get("show_after", 0)), 0.0)

    def draw(self, frame, fraction, elapsed=None, index=0, count=0):
        """Paint the indicator onto `frame` in place.

        `fraction` is dwell progress in 0..1, or None to hide (transitions,
        fallback frames). `elapsed` gates show_after; `index`/`count` locate
        the current app in the rotation — with fewer than two apps there is
        no next screen, so nothing is drawn.
        """
        if not self.enabled or fraction is None or count < 2:
            return
        if elapsed is not None and elapsed < self.show_after:
            return
        fraction = min(max(fraction, 0.0), 1.0)
        shown = 1.0 - fraction if self.direction == "drain" else fraction
        if self.style == "dots":
            self._draw_dots(frame, shown, index, count)
        elif self.style == "arc":
            self._draw_arc(frame, shown)
        else:
            self._draw_bar(frame, shown)

    # ------------------------------------------------------------------
    def _draw_bar(self, frame, shown):
        width = frame.width
        y0 = 0 if self.position == "top" else frame.height - self.thickness
        lit = int(round(shown * width))
        px = frame.load()
        for y in range(y0, y0 + self.thickness):
            for x in range(width):
                px[x, y] = self.color if x < lit else self.track

    def _draw_dots(self, frame, shown, index, count):
        # 2x2 dots on a 4px pitch, centered along the bottom edge, 1px in.
        # The current dot ramps from 40% to full color across the dwell —
        # floored so "which screen am I on" stays readable at fraction 0.
        pitch, dot = 4, 2
        total = count * dot + (count - 1) * (pitch - dot)
        x0 = max((frame.width - total) // 2, 0)
        y0 = frame.height - dot - 1
        px = frame.load()
        for i in range(count):
            color = self.track
            if i == index:
                color = _lerp(self.track, self.color, 0.4 + 0.6 * shown)
            dx = x0 + i * pitch
            for x in range(dx, min(dx + dot, frame.width)):
                for y in range(y0, y0 + dot):
                    px[x, y] = color
        return

    def _draw_arc(self, frame, shown):
        # 7x7 ring on a black backdrop tile, bottom-right, lit clockwise from
        # twelve o'clock. 16 sweep positions — plenty at this size.
        ox = frame.width - 7 - _ARC_MARGIN
        oy = frame.height - 7 - _ARC_MARGIN
        px = frame.load()
        for x in range(ox - 1, min(ox + 8, frame.width)):
            for y in range(oy - 1, min(oy + 8, frame.height)):
                px[x, y] = (0, 0, 0)
        lit = int(round(shown * len(_ARC_RING)))
        for i, (x, y) in enumerate(_ARC_RING):
            px[ox + x, oy + y] = self.color if i < lit else self.track
