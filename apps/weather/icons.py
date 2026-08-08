"""Procedural weather icons for the weather app.

Each icon is drawn with stock Pillow shapes within roughly a 40x40 box centered on
(cx, cy), so there are no image assets to ship. `category()` maps a WMO weather code
to an icon name; `draw_icon()` renders that icon.

Icons animate across three states, selected by `phase` (0, 1, 2). Each icon cycles
0 -> 1 -> 2 -> 0, so the motion has to read as a *loop*: anything that simply
progresses across the three states snaps visibly on the way back to 0. Falling
precipitation sidesteps that by wrapping — a drop leaving the bottom is the same
pixel as a new drop entering the top — while the sun, stars and storm cycle through
states that have no inherent order at all.

Nothing here translates: shapes hold still and animate by brightness or by internal
detail. A whole icon sliding a pixel at this scale reads as a wobble, not as motion.
"""

from PIL import Image, ImageDraw

PHASES = 3

_SUN = (255, 200, 60)
_MOON = (200, 214, 235)
_CLOUD = (210, 210, 215)
_CLOUD_DARK = (120, 124, 132)
_RAIN = (90, 170, 255)
_SNOW = (225, 235, 245)
_BOLT = (255, 224, 70)
_BOLT_DIM = (150, 130, 40)
_FOG = (150, 150, 155)

# Star colors. The core is always lit at full brightness — a star that blinks out
# entirely reads as a dead pixel, so the twinkle is carried by the arms instead.
_STAR_CORE = (245, 248, 255)
_STAR_ARM = (185, 196, 225)
_STAR_ARM_DIM = (110, 120, 148)
_STAR_TIP = (70, 76, 96)

# The standard lightning outline, normalized (x right, y down) and scaled at draw
# time. Drawn as a polygon rather than a hand-plotted bitmap: a bolt is a solid
# wedge, and thin plotted diagonals just staircase into a scribble at this size.
# Proportion matters more than the shape — the same outline at 13 wide reads as a
# squat blob, and only turns into lightning once it's clearly taller than it is wide.
_BOLT_OUTLINE = [(0.62, 0.00), (0.10, 0.55), (0.45, 0.55),
                 (0.30, 1.00), (0.90, 0.42), (0.52, 0.42)]
_BOLT_W, _BOLT_H = 8, 13
# Where the bolt starts, relative to the icon center. The cloud's base sits at cy+6,
# so this hides the bolt's top rows behind it while leaving most of the zigzag below.
_BOLT_TOP = 5

# Sun ray length per state. Not monotonic: it breathes out and settles back rather
# than growing and snapping. The short set is for the small sun peeking out from
# behind a cloud, whose beams would otherwise run out past the icon.
_RAY_REACH = (5, 8, 6)
_RAY_REACH_SMALL = (3, 5, 4)

# Fixed stars around the crescent, each shimmering on its own offset. All sit clear
# of the disc — a star crowding the rim reads as a stray lit pixel, not as a star.
_STARS = ((-15, -13), (13, -9), (-17, 7), (11, 11))

# Half-lengths of the three fog bands. They differ so the bands read as drifting
# mist, but each is centered on the icon rather than hanging off to one side.
_FOG_HALF = (12, 8, 10)

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


def draw_icon(image, cx, cy, kind, is_day, phase=0):
    """Draw the icon for `kind` centered on (cx, cy) of `image`, in state `phase`."""
    phase %= PHASES
    draw = ImageDraw.Draw(image)

    if kind == "clear":
        if is_day:
            _sun(draw, cx, cy, phase=phase)
        else:
            _moon(draw, cx, cy, phase=phase)
    elif kind == "partly":
        # Beams too — drawn before the cloud, so the cloud occludes the ones behind
        # it and the sun genuinely reads as peeking out from behind.
        if is_day:
            _sun(draw, cx - 8, cy - 8, r=8, phase=phase, reaches=_RAY_REACH_SMALL)
        else:
            _moon(draw, cx - 8, cy - 8, r=8, stars=False)
        _cloud(image, cx + 2, cy + 4, phase=phase)
    elif kind == "fog":
        _cloud(image, cx, cy - 2, phase=phase)
        # Bands of differing length easing past each other — mist, not stripes. The
        # travel is deliberately small; more than a pixel or two per state and it
        # stops looking like fog and starts looking like a scrolling marquee.
        for i, half in enumerate(_FOG_HALF):
            y = cy + 10 + i * 4
            shift = ((phase + i) % PHASES) - 1
            draw.line([(cx - half + shift, y), (cx + half + shift, y)], fill=_FOG)
    elif kind in ("drizzle", "rain"):
        _cloud(image, cx, cy - 4, phase=phase)
        _precip(draw, cx, cy, phase, drops=3 if kind == "drizzle" else 4, snow=False)
    elif kind == "snow":
        _cloud(image, cx, cy - 4, phase=phase)
        _precip(draw, cx, cy, phase, drops=4, snow=True)
    elif kind == "storm":
        # Bolt first, cloud over it: the cloud hides the bolt's top few rows so it
        # strikes out from behind rather than being pasted on top of the cloud.
        # Flash: struck, glowing, gone. Dropping a state entirely is what makes it
        # read as lightning rather than as a bolt that fades.
        color = (_BOLT, _BOLT_DIM, None)[phase]
        if color:
            draw.polygon(
                [(round(cx + (px - 0.5) * _BOLT_W), round(cy + _BOLT_TOP + py * _BOLT_H))
                 for px, py in _BOLT_OUTLINE],
                fill=color,
            )
        _cloud(image, cx, cy - 4, color=_CLOUD_DARK, phase=phase)
    else:  # cloudy
        _cloud(image, cx, cy, phase=phase)


def _precip(draw, cx, cy, phase, drops, snow):
    """Falling rain or snow. Each column's particle advances a third of the fall per
    phase and wraps, so state 2 -> 0 is continuous rather than a jump back up."""
    top, span = cy + 8, 12
    step = span // PHASES
    for i in range(drops):
        x = cx - 12 + i * 8
        # Stagger columns so they don't fall in lockstep.
        y = top + ((phase + i) * step) % span
        if snow:
            # Flakes sway as they fall instead of dropping straight.
            x += (-1, 0, 1)[(phase + i) % PHASES]
            draw.ellipse([x - 1, y, x + 1, y + 2], fill=_SNOW)
        else:
            draw.line([(x, y), (x - 3, y + 5)], fill=_RAIN)


def _sun(draw, cx, cy, r=10, rays=True, phase=0, reaches=_RAY_REACH):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_SUN)
    if not rays:
        return
    reach = reaches[phase]  # every ray the same length within a state
    for dx, dy in _RAY_DIRS:
        x0, y0 = cx + dx * (r + 3), cy + dy * (r + 3)
        x1, y1 = cx + dx * (r + reach), cy + dy * (r + reach)
        draw.line([(x0, y0), (x1, y1)], fill=_SUN)


def _moon(draw, cx, cy, r=10, stars=True, phase=0):
    # Crescent: a lit disc with an offset black disc bitten out of it.
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_MOON)
    draw.ellipse([cx - r + 5, cy - r, cx + r + 5, cy + r], fill="black")
    if not stars:
        return
    # Every star holds its position and twinkles in place, each on its own offset so
    # they sparkle out of step rather than blinking together. An earlier version had
    # one star roam between three spots, but a star that moves has to vacate where it
    # was — which reads as two neighbours blinking out, not as one star travelling.
    for i, (sx, sy) in enumerate(_STARS):
        _star(draw, cx + sx, cy + sy, (phase + i) % PHASES)


def _star(draw, x, y, level):
    """A four-point sparkle that twinkles by growing rather than by fading.

    `level` 0 is the bare core, 1 adds faint arms, 2 opens into a full sparkle. The
    core stays fully lit at every level — a star that blinks out entirely reads as a
    dead pixel on the panel rather than as a star.
    """
    if level:
        arm = _STAR_ARM_DIM if level == 1 else _STAR_ARM
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            draw.point((x + dx, y + dy), fill=arm)
    if level == 2:
        # Faint diagonals at full size round the sparkle out to eight points.
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            draw.point((x + dx, y + dy), fill=_STAR_TIP)
    draw.point((x, y), fill=_STAR_CORE)


def _cloud(image, cx, cy, color=_CLOUD, phase=0):
    """A stationary cloud whose upper edge shimmers, as if the top were wisping away.

    Drawn into a scratch tile so the topmost lit pixel of each column can be found by
    scanning; dimming a travelling band of those reads as translucency at the edge
    without the silhouette ever moving.
    """
    width, height = 34, 22
    lx, ly = 17, 10  # cloud center within the tile
    tile = Image.new("RGB", (width, height), (0, 0, 0))
    pen = ImageDraw.Draw(tile)
    pen.ellipse([lx - 15, ly - 2, lx - 3, ly + 10], fill=color)
    pen.ellipse([lx - 6, ly - 8, lx + 8, ly + 8], fill=color)
    pen.ellipse([lx + 3, ly - 2, lx + 15, ly + 10], fill=color)
    pen.rectangle([lx - 13, ly + 4, lx + 13, ly + 10], fill=color)

    # Dim a scattering of single edge pixels — one column in six, one row deep. A
    # wider band, or dimming the row beneath as well, thickens the edge into
    # something that ripples like flame rather than shimmering.
    faint = tuple(int(c * 0.75) for c in color)
    px = tile.load()
    for x in range(width):
        for y in range(height):
            if px[x, y] == (0, 0, 0):
                continue
            if (x + phase * 2) % 6 == 0:  # travels 2px per state, wraps every 3
                px[x, y] = faint
            break  # only the topmost lit pixel of each column

    # Paste with black masked out so the tile doesn't punch a hole in the icon.
    image.paste(tile, (cx - lx, cy - ly), tile.convert("L").point(lambda v: 255 if v else 0))
