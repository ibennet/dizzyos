"""Procedural weather icons for the weather app.

Each icon is drawn with stock Pillow shapes within roughly a 40x40 box centered on
(cx, cy), so there are no image assets to ship. `category()` maps a WMO weather code
to an icon name; `draw_icon()` renders that icon.
"""

_SUN = (255, 200, 60)
_MOON = (200, 214, 235)
_CLOUD = (210, 210, 215)
_CLOUD_DARK = (120, 124, 132)
_RAIN = (90, 170, 255)
_SNOW = (225, 235, 245)
_BOLT = (255, 224, 70)

# Unit-ish direction vectors for the 8 sun rays (diagonals scaled to ~0.7).
_RAY_DIRS = [
    (1, 0), (0, 1), (-1, 0), (0, -1),
    (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7),
]


def category(code):
    """Map a WMO weather code to an icon category."""
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "cloudy"  # 3 (overcast) and any unrecognized code


def draw_icon(draw, cx, cy, kind, is_day):
    if kind == "clear":
        (_sun if is_day else _moon)(draw, cx, cy)
    elif kind == "partly":
        if is_day:
            _sun(draw, cx - 8, cy - 8, r=8, rays=False)
        else:
            _moon(draw, cx - 8, cy - 8, r=8)
        _cloud(draw, cx + 2, cy + 4)
    elif kind == "fog":
        _cloud(draw, cx, cy - 2)
        for i in range(3):
            y = cy + 10 + i * 4
            draw.line([(cx - 15, y), (cx + 15, y)], fill=(150, 150, 155))
    elif kind in ("drizzle", "rain"):
        _cloud(draw, cx, cy - 4)
        drops = 3 if kind == "drizzle" else 4
        for i in range(drops):
            x = cx - 12 + i * 8
            draw.line([(x, cy + 10), (x - 3, cy + 17)], fill=_RAIN)
    elif kind == "snow":
        _cloud(draw, cx, cy - 4)
        for i in range(4):
            x = cx - 12 + i * 8
            draw.ellipse([x - 1, cy + 11, x + 1, cy + 13], fill=_SNOW)
    elif kind == "storm":
        _cloud(draw, cx, cy - 4, color=_CLOUD_DARK)
        draw.polygon(
            [(cx, cy + 9), (cx - 5, cy + 17), (cx - 1, cy + 17),
             (cx - 3, cy + 22), (cx + 5, cy + 13), (cx + 1, cy + 13)],
            fill=_BOLT,
        )
    else:  # cloudy
        _cloud(draw, cx, cy)


def _sun(draw, cx, cy, r=10, rays=True):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_SUN)
    if not rays:
        return
    for dx, dy in _RAY_DIRS:
        x0, y0 = cx + dx * (r + 3), cy + dy * (r + 3)
        x1, y1 = cx + dx * (r + 7), cy + dy * (r + 7)
        draw.line([(x0, y0), (x1, y1)], fill=_SUN)


def _moon(draw, cx, cy, r=10):
    # Crescent: a lit disc with an offset black disc bitten out of it.
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_MOON)
    draw.ellipse([cx - r + 5, cy - r, cx + r + 5, cy + r], fill="black")


def _cloud(draw, cx, cy, color=_CLOUD):
    # Three overlapping puffs with a flat base.
    draw.ellipse([cx - 15, cy - 2, cx - 3, cy + 10], fill=color)
    draw.ellipse([cx - 6, cy - 8, cx + 8, cy + 8], fill=color)
    draw.ellipse([cx + 3, cy - 2, cx + 15, cy + 10], fill=color)
    draw.rectangle([cx - 13, cy + 4, cx + 13, cy + 10], fill=color)
