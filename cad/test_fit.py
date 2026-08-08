#!/usr/bin/env python3
"""Fit checks for the sign enclosure — does the design actually go together?

Every dimension in the build guide is derived in `params.py`; this asserts the
relationships between them still hold. The point is that changing a parameter
(a different panel pitch, thicker stock, a Pi 4 instead of a 3B+) fails loudly
here instead of at the workbench with the wood already cut.

Run directly, or via dev/check.sh. No dependencies.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cad.params import CASE, fraction, inches  # noqa: E402

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

print("panel block in the opening")
check("panels fit the opening width",
      c.opening_width >= c.panels_width,
      f"opening {c.opening_width:.1f} < panels {c.panels_width:.1f} mm")
check("panels fit the opening height",
      c.opening_height >= c.panels_height,
      f"opening {c.opening_height:.1f} < panels {c.panels_height:.1f} mm")
check("clearance is present but not sloppy",
      0 < c.clearance <= 6,
      f"clearance {c.clearance} mm — under 6 mm or the panels wander")

print("depth stack")
# The standoffs set panel face flush with the acrylic. A negative or zero value
# means the panels are deeper than the frame — no standoff can fix that.
check("standoffs have positive length",
      c.standoff_length > 0,
      f"panel depth {c.panel.depth} mm exceeds frame depth {c.interior_depth:.1f} mm")
check("standoff length is buyable",
      10 <= c.standoff_length <= 60,
      f"{c.standoff_length:.1f} mm — outside the range sold as stock spacers")
check("Pi stack clears the panels",
      c.stack_headroom > 0,
      f"stack {c.electronics.stack_depth:.1f} mm exceeds cavity "
      f"{c.cavity_behind_panels:.1f} mm")
check("cable headroom behind the stack",
      c.stack_headroom >= 8,
      f"only {c.stack_headroom:.1f} mm — ribbons need room to turn")

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

print()
print(c.report())
print()

if failures:
    print(f"fit: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"fit: all {passed} checks passed")
