#!/usr/bin/env python3
"""Fit checks for the sign enclosure — does the design actually go together?

Every dimension in the build guide is derived in `params.py`; this asserts the
relationships between them still hold. The point is that changing a parameter
(a different panel pitch, thicker stock, a Pi 4 instead of a 3B+) fails loudly
here instead of at the workbench with the wood already cut.

Run directly, or via dev/check.sh. No dependencies.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cad.params import (BACK_FACE_HARDWARE, BRACE_THICKNESS,  # noqa: E402
                        CASE, HANGER_SAFETY_FACTOR, MAX_BACK_PROTRUSION,
                        MIN_NUT_ENGAGEMENT, MIN_THREAD_ENGAGEMENT,
                        REJECTED_BACK_HARDWARE, STOCK_STANDOFFS,
                        fraction, inches)

passed = 0
failures = []


def check(name, condition, detail=""):
    global passed
    if condition:
        passed += 1
        print(f"  ok: {name}")
    else:
        failures.append(f"{name}{' — ' + detail if detail else ''}")
        print(f"  FAIL: {name}{' — ' + detail if detail else ''}")


c = CASE

print("the seam between panels")
# The one dimension with no tolerance at all. Pixels sit half a pitch in from
# each panel edge, so butted panels continue the grid across the seam at exactly
# one pitch; a gap shows as a dead stripe through the middle of every frame.
check("panels butt with no gap",
      c.panel_gap == 0,
      f"panel_gap {c.panel_gap} mm — the pixel grid breaks at the seam")
check("the seam keeps the pixel pitch",
      abs((c.panels_width / (c.canvas_pixels[0])) - c.panel.pitch) < 1e-9,
      f"effective pitch {c.panels_width / c.canvas_pixels[0]:.4f} mm "
      f"vs panel pitch {c.panel.pitch} mm")
check("canvas matches what the kernel renders",
      c.canvas_pixels == (128, 64),
      f"canvas {c.canvas_pixels} — config.yaml expects 128x64")

print("panel block in the opening")
check("panels fit the opening width",
      c.opening_width >= c.panels_width,
      f"opening {c.opening_width:.1f} < panels {c.panels_width:.1f} mm")
check("panels fit the opening height",
      c.opening_height >= c.panels_height,
      f"opening {c.opening_height:.1f} < panels {c.panels_height:.1f} mm")
check("clearance survives rounding to the cut grid",
      2 <= c.actual_clearance_x <= 8 and 2 <= c.actual_clearance_y <= 8,
      f"x {c.actual_clearance_x:.2f} mm, y {c.actual_clearance_y:.2f} mm — want 2-8 mm")

# The cut desk works in eighths. Anything finer is a number they cannot hit and
# you cannot check, so every wood dimension must land on the grid exactly.
for label, value in (("opening width", c.opening_width),
                     ("opening height", c.opening_height),
                     ("rail length", c.rail_length),
                     ("stile length", c.stile_length)):
    eighths = inches(value) * 8
    check(f"{label} lands on the cut grid",
          abs(eighths - round(eighths)) < 1e-6,
          f"{inches(value):.4f} in is not a whole eighth")

print("depth stack")
# The standoffs set panel face flush with the acrylic. A negative or zero value
# means the panels are deeper than the frame — no standoff can fix that.
check("standoffs have positive length",
      c.standoff_ideal > 0,
      f"panel depth {c.panel.depth} mm exceeds frame depth {c.interior_depth:.1f} mm")
# The exact length is almost never a length anyone sells, so the design has to
# name a stock part — and it must be the next size DOWN. A standoff longer than
# the cavity stands the panels proud of the frame, where they press into the
# acrylic and bow it; a shorter one just leaves them sitting behind it.
check("the standoff is a size you can buy",
      c.standoff_length in [s[0] for s in STOCK_STANDOFFS],
      f"{c.standoff_length} mm is not a stock length")
check("the standoff says what to buy",
      bool(c.standoff_source),
      "no source recorded for the chosen standoff")
check("the standoff fits the cavity",
      c.standoff_length <= c.standoff_ideal,
      f"stock {c.standoff_length} mm exceeds the {c.standoff_ideal:.1f} mm cavity — "
      f"the panels would press into the acrylic")
# Some recess is unavoidable once you round to stock; too much and the diffuser
# is far enough off the LEDs to soften the pixels noticeably.
check("panel sits close behind the acrylic",
      c.panel_recess <= 5,
      f"{c.panel_recess:.1f} mm behind the face — stock standoffs leave too big a gap")
check("Pi stack clears the panels",
      c.stack_headroom > 0,
      f"stack {c.electronics.stack_depth:.1f} mm exceeds cavity "
      f"{c.cavity_behind_panels:.1f} mm")
check("cable headroom behind the stack",
      c.stack_headroom >= 8,
      f"only {c.stack_headroom:.1f} mm — ribbons need room to turn")

print("getting the back assembly in and out")
# Everything mounts to the back board, so it comes out backwards — dragging the
# panels through the full depth of the cavity. That makes the frame's inner
# faces a swept volume, and the clearance around the panels the entire budget
# for anything screwed to them.
check("the panels can pass through the opening at all",
      c.inner_face_clearance > 0,
      f"{c.inner_face_clearance:.2f} mm per side — the assembly cannot come out")
# This one is expected to hold in the negative: there is NOT room, which is why
# the design must keep the cavity bare. Asserting it stops someone reading the
# spare 2 mm as somewhere to put a bracket.
check("the cavity is too tight for inner-face hardware — keep it bare",
      c.inner_face_clearance < BRACE_THICKNESS,
      f"{c.inner_face_clearance:.2f} mm per side now exceeds a "
      f"{BRACE_THICKNESS} mm brace — the guide may say to fit them again")

print("hanging it on a wall")
check("the hanger is rated for the sign with margin",
      c.hanger[1] >= c.estimated_mass * HANGER_SAFETY_FACTOR,
      f"{c.hanger[0]} is rated {c.hanger[1]:.0f} kg; "
      f"{c.estimated_mass:.1f} kg × {HANGER_SAFETY_FACTOR:.0f} wants "
      f"{c.estimated_mass * HANGER_SAFETY_FACTOR:.1f} kg")
# The corners are the shear path. Magnets carry none, so however many catches
# get added, the screws at the corners cannot be traded away for them.
check("the corners are still screwed, not latched",
      c.back_corner_screws >= 4,
      f"only {c.back_corner_screws} corner screws — the frame can rack, and "
      f"corner braces cannot be added to compensate")
# Wall-hung: anything proud of the back face rocks the case against the plaster.
# This is the constraint that rules out the latches worth wanting.
for name, proud in BACK_FACE_HARDWARE:
    check(f"{name} sits flush enough for a wall",
          proud <= MAX_BACK_PROTRUSION,
          f"{proud} mm proud, over the {MAX_BACK_PROTRUSION} mm budget")
# And the converse, so the reason the nice latches are absent stays on record
# rather than looking like an oversight someone should helpfully fix.
for name, proud in REJECTED_BACK_HARDWARE:
    check(f"{name} is correctly ruled out",
          proud > MAX_BACK_PROTRUSION,
          f"{name} would now fit in {MAX_BACK_PROTRUSION} mm — reconsider it")
check("opening the case is a short job",
      c.opening_fasteners <= 6,
      f"{c.opening_fasteners} fasteners to undo before you reach the Pi")

print("panel mounting screws")
# The inlets are threaded and blind, so the screw is caught between reaching
# and bottoming out. The build sheet originally called for M3 × 16 mm against a
# 50.8 mm span — 35 mm short of even touching the panel.
check("a stock M3 screw fits the stack",
      MIN_THREAD_ENGAGEMENT <= c.panel_screw_engagement
      <= c.panel.mount_thread_depth,
      f"M3 × {c.panel_screw_length:.0f} mm engages "
      f"{c.panel_screw_engagement:.1f} mm of a "
      f"{c.panel.mount_thread_depth:.0f} mm inlet across a "
      f"{c.panel_screw_span:.1f} mm span")
check("the screw clears the back board and spacers",
      c.panel_screw_length > c.panel_screw_span,
      f"M3 × {c.panel_screw_length:.0f} mm cannot cross "
      f"{c.panel_screw_span:.1f} mm of back board and spacers")
# The Pi is the same sandwich but with a nut on top, since its holes go through.
check("a stock M2.5 screw clears the Pi stack",
      c.pi_screw_length >= c.pi_screw_span + MIN_NUT_ENGAGEMENT,
      f"M2.5 × {c.pi_screw_length:.0f} mm leaves "
      f"{c.pi_screw_length - c.pi_screw_span:.1f} mm proud of a "
      f"{c.pi_screw_span:.1f} mm stack — not enough to start a nut")

print("material yield")
check("frame comes out of one board",
      c.board_length_needed <= c.stock.board_length,
      f"need {inches(c.board_length_needed):.1f} in of "
      f"{inches(c.stock.board_length):.0f} in")
check("face comes out of one acrylic sheet",
      c.outer_width <= max(c.stock.acrylic_sheet)
      and c.outer_height <= min(c.stock.acrylic_sheet),
      f"{fraction(c.outer_width)} × {fraction(c.outer_height)} vs sheet "
      f"{fraction(max(c.stock.acrylic_sheet))} × {fraction(min(c.stock.acrylic_sheet))}")
check("back comes out of one plywood panel",
      c.outer_width <= max(c.stock.plywood_sheet)
      and c.outer_height <= min(c.stock.plywood_sheet))

print("electronics footprint")
check("Pi footprint fits inside the opening",
      c.electronics.pi_width <= c.opening_width
      and c.electronics.pi_length <= c.opening_height)

print("cut list is sane")
for part, qty, size in c.cut_list():
    check(f"{part} has a size", bool(size) and "0 in" not in size, size)

# The build sheet says twice that its drawings cannot drift from this module.
# They can, and did: its 3D model carried hand-typed millimetres and its buy
# list quoted a 2 in standoff against a 48.5 mm cavity. Nothing connects a
# published HTML page to a Python module except a check that reads both.
print("build sheet agrees with the model")
SHEET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "build-sheet.html")
if not os.path.exists(SHEET):
    print(f"  skip: no build sheet at {SHEET}")
else:
    sheet = open(SHEET, encoding="utf-8").read()
    # Pull the constants back out of the page's own model script.
    consts = dict(re.findall(r"(\bW|\bH|\bD|PANEL|PANEL_D|BACK|ACRYLIC|STANDOFF)"
                             r"\s*=\s*([0-9.]+)", sheet))
    for name, expected in (("W", c.outer_width),
                           ("H", c.outer_height),
                           ("D", c.interior_depth),
                           ("PANEL", c.panel.width),
                           ("PANEL_D", c.panel.depth),
                           ("BACK", c.stock.back_thickness),
                           ("STANDOFF", c.standoff_length)):
        got = consts.get(name)
        check(f"sheet's {name} matches params.py",
              got is not None and abs(float(got) - expected) < 0.05,
              f"sheet says {got}, params.py derives {expected:.2f}")
    # The cut list is the part someone hands to a lumber desk, so check the
    # strings the page actually prints. It sets them as typographic fractions
    # (16¾) where `fraction()` emits ASCII (16 3/4); normalise before comparing
    # rather than teaching either side about the other's spelling.
    VULGAR = {"¼": " 1/4", "½": " 1/2", "¾": " 3/4", "⅛": " 1/8",
              "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8", "⁹⁄₁₆": " 9/16"}
    flat = sheet
    for glyph, ascii_ in VULGAR.items():
        flat = flat.replace(glyph, ascii_)
    for label, value in (("rail", c.rail_length),
                         ("stile", c.stile_length),
                         ("opening width", c.opening_width),
                         ("opening height", c.opening_height)):
        # The unit is optional: the sheet writes paired dimensions as
        # "15 1/4 × 7 3/4 in", so only the last of the pair carries the "in".
        # The leading guard stops 15 1/4 matching inside 115 1/4.
        bare = fraction(value).replace(" in", "")
        check(f"sheet quotes the {label} as {fraction(value)}",
              re.search(r"(?<![\d.])" + re.escape(bare) + r"(?![\d/])", flat)
              is not None,
              f"{fraction(value)} does not appear in the build sheet")
    # The buy list originally said M3 × 16 mm against a 50.8 mm span — a screw
    # that could not reach the panel. Nothing caught it because nothing checked
    # the fastener at all, so check the one the sheet actually tells you to buy.
    screw = f"M3 × {c.panel_screw_length:.0f} mm"
    check(f"sheet quotes the panel screw as {screw}",
          screw in sheet,
          f"{screw} does not appear in the build sheet")
    check("sheet asks for one spacer stack per mounting hole",
          str(c.spacers_needed) in sheet,
          f"the sheet never mentions needing {c.spacers_needed} of them")
    # The sheet promised "eight screws" to open the back against a 52 in
    # perimeter. That number sets how hard the Pi is to reach, so state it once
    # and derive it — an optimistic count here reads as a design that is easier
    # to service than it is.
    check(f"sheet says {c.opening_fasteners} screws open the back",
          str(c.opening_fasteners) in sheet,
          f"the sheet does not say the back opens with "
          f"{c.opening_fasteners} screws")
    check("sheet names the wall fixing",
          c.hanger[0] in sheet,
          f"the sheet never says to hang it on {c.hanger[0]}")
    # The scroll animation maps each step to a model stage through a hardcoded
    # array. Insert a step anywhere and every stage after it shifts by one, so
    # the model quietly illustrates the wrong instruction — no error, just a
    # drawing that no longer matches the words. Counting is the only defence.
    steps = (sheet[sheet.index('<ol class="steps">'):]
             if '<ol class="steps">' in sheet else "")
    n_steps = len(re.findall(r"<li>", steps))
    gbs = re.search(r"GROUP_BY_STEP = \[(.*?)\]", sheet, re.S)
    n_stages = len(re.findall(r"\d+", re.sub(r"//[^\n]*", "", gbs.group(1)))) \
        if gbs else 0
    check("the animation has a stage for every step",
          n_steps == n_stages,
          f"{n_steps} steps but {n_stages} entries in GROUP_BY_STEP — "
          f"the model illustrates the wrong step from the mismatch onward")

    # The stage numbers live in three places that must agree: the step map, the
    # parts table, and the reduced-motion rest pose. Each was edited by hand.
    stage_nums = [int(n) for n in
                  re.findall(r"\d+", re.sub(r"//[^\n]*", "", gbs.group(1)))] \
        if gbs else []
    # Everything from the parts literal to the face builder — the generators
    # that follow the literal declare stages too, so reading only the literal
    # under-reports and wrongly calls a generated stage idle.
    parts = re.search(r"var PARTS = \[(.*?)var FACES =", sheet, re.S)
    body = parts.group(1) if parts else ""
    appears = [int(n) for n in re.findall(r"\bg:\s*(\d+)", body)]
    seats = [int(n) for n in re.findall(r"\bgi:\s*(\d+)", body)]
    mids = [int(n) for n in re.findall(r"\bmid:\s*\[\s*(\d+)", body)]
    rest = re.search(r"place\(reduced \? (\d+) : 0\)", sheet)
    top_step = max(stage_nums) if stage_nums else -1
    top_part = max(appears + seats + mids) if appears else -1

    check("the last step lands on the last stage",
          top_step == top_part,
          f"steps run to stage {top_step} but the parts table to {top_part}")
    check("reduced motion rests on the finished sign",
          rest is not None and int(rest.group(1)) == top_part,
          f"rests at {rest.group(1) if rest else 'nothing'}, "
          f"but the build finishes at {top_part}")
    # The drawing declares how many of each fastener it renders. It showed four
    # panel spacers for a long time against a design needing eight, and no
    # fasteners at all — so tie the declaration to the derived counts.
    cblock = re.search(r"var COUNTS = \{(.*?)\};", sheet, re.S)
    counts = dict((k, int(v)) for k, v in
                  re.findall(r"(\w+):\s*(\d+)", cblock.group(1))) \
        if cblock else {}
    e = c.electronics
    for key, want, why in (
            ("panelSpacers", c.spacers_needed, "one per panel mounting hole"),
            ("panelScrews", c.spacers_needed, "one M3 up through each stack"),
            ("piSpacers", e.pi_mount_holes, "one per Pi mounting hole"),
            ("piScrews", e.pi_mount_holes, "one per Pi mounting hole"),
            ("piNuts", e.pi_mount_holes, "the Pi's holes go through"),
            ("backScrews", c.back_corner_screws, "the corners are the shear path"),
            ("backMagnets", c.back_magnets, "mid-span on the long sides"),
            ("frameScrews", c.frame_corner_screws, "two per frame corner"),
            ("faceScrews", c.face_screws, "one per corner of the face"),
            ("hangers", c.hanger_count, "a pair, so it hangs level")):
        check(f"drawing shows {want} {key}",
              counts.get(key) == want,
              f"drawing declares {counts.get(key)}, design needs {want} — {why}")

    # A stage nothing happens in is a stretch of text with a frozen drawing —
    # the thing this whole animation exists to avoid.
    active = set(appears) | set(seats) | set(mids)
    idle = [s for s in range(1, top_part + 1) if s not in active]
    check("every stage moves something",
          not idle,
          f"stage(s) {idle} introduce and move nothing — the model freezes "
          f"while the steps keep scrolling")
    # The guide used to say "screw a flat L-bracket across each inside corner",
    # which at 2 mm would foul panels that have 1.67 mm a side. Geometry alone
    # cannot catch prose, so check the prose.
    # Match an instruction to fit one, not any mention — the sheet SHOULD warn
    # about braces, and a bare substring test flags its own warning.
    fitting = re.search(r"(screw|fit|attach|install|add)\b[^.]{0,40}"
                        r"(corner brace|l-bracket)", steps, re.I)
    check("no assembly step puts hardware inside the cavity",
          fitting is None,
          f"a step says {fitting.group(0)!r} — the panels sweep those faces"
          if fitting else "")

print()
print(c.report())
print()

if failures:
    print(f"fit: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"fit: all {passed} checks passed")
