"""Parametric dimension model for the sign enclosure.

Single source of truth for every number in the build: the cut list, the drawings
in the build guide, and the fit checks in `test_fit.py` all derive from the
constants here. Change the panel size or the stock and everything downstream
follows — including the assertions that say whether it still fits.

Deliberately dependency-free. Solid geometry (build123d/CadQuery) is a separate,
optional layer: it would import this module rather than restate any of it, so the
fit checks keep running in CI without a CAD kernel installed.

Units are millimetres throughout — the panels are metric and the lumber is not,
so one of them has to convert, and it may as well be the one sold in round
fractions. `inches()` renders values back for the hardware store.
"""

import math
from dataclasses import dataclass

MM_PER_INCH = 25.4

#: Cut dimensions round UP to this fraction of an inch. A hardware-store panel
#: saw holds about an eighth; specifying sixteenths asks for precision the cut
#: desk cannot deliver and you cannot verify, and it turns every dimension into
#: an awkward number for someone reading a tape measure. Rounding up rather than
#: to-nearest means the opening only ever grows, so the panels always still fit.
CUT_GRID = 8


def inches(mm):
    """Millimetres as a decimal-inch float."""
    return mm / MM_PER_INCH


def snap_up(mm, denominator=CUT_GRID):
    """Round `mm` up to the next 1/`denominator` inch, returned in mm."""
    return math.ceil(inches(mm) * denominator) / denominator * MM_PER_INCH


def fraction(mm, denominator=16):
    """Millimetres as a shop-friendly inch string, e.g. '16 3/4 in'.

    Rounded to `denominator`ths because that is the finest gradation on a tape
    measure — a cut list quoting 16.7362 in is a cut list nobody can follow.
    """
    total = round(inches(mm) * denominator)
    whole, part = divmod(total, denominator)
    if part == 0:
        return f"{whole} in"
    # Reduce the fraction so we print 3/4 rather than 12/16.
    a, b = part, denominator
    while b:
        a, b = b, a % b
    return f"{whole} {part // a}/{denominator // a} in" if whole else f"{part // a}/{denominator // a} in"


@dataclass(frozen=True)
class Panel:
    """One HUB75 LED panel."""

    pixels_x: int = 64
    pixels_y: int = 64
    pitch: float = 3.0          # P3 — 3 mm between pixel centres
    #: Front face to back of the PCB, ignoring cable connectors. Measure yours
    #: with calipers; panel depth varies by manufacturer more than any other
    #: number here, and it sets the standoff length.
    depth: float = 15.0
    #: Mounting points on the back. Adafruit's 64×64 P3 (4732) has four.
    mount_holes: int = 4
    #: These are threaded M3 inlets, NOT through-holes — the screw threads into
    #: the panel and takes no nut. That is why the mounting screw has to span
    #: the back board and the whole spacer stack before it engages anything,
    #: and why it can bottom out: the thread is only so deep. Measure yours.
    mount_thread_depth: float = 6.0

    @property
    def width(self):
        return self.pixels_x * self.pitch

    @property
    def height(self):
        return self.pixels_y * self.pitch


#: Standoff lengths obtainable in one Home Depot trip, and what to buy for each.
#:
#: Their nylon spacers (Everbilt) stop at 1 in, so every length past that is a
#: stack rather than a part — which is why the source rides along with the
#: number instead of living in the build guide, where it would drift.
#:
#: Ordering M3 standoffs online would give an exact 48.5 mm in one rigid piece,
#: which is mechanically better; this list is the deliberate trade of that for a
#: build that needs no shipping. Swapping back means replacing this tuple, and
#: everything downstream — buy list, recess, drawings — follows.
STOCK_STANDOFFS = (
    (12.70, '1/2 in nylon spacer'),
    (19.05, '3/4 in nylon spacer'),
    (25.40, '1 in nylon spacer'),
    (31.75, '1 in + 1/4 in spacers stacked'),
    (38.10, '1 in + 1/2 in spacers stacked'),
    (44.45, '1 in + 3/4 in spacers stacked'),
    (50.80, '2 × 1 in spacers stacked'),
)

#: M3 machine screw lengths sold in the fastener aisle, in mm.
STOCK_M3_SCREWS = (6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)

#: Thread engaged in the panel's inlet, at minimum. Below roughly one screw
#: diameter the threads strip out of a soft insert before the joint is tight.
MIN_THREAD_ENGAGEMENT = 3.0

#: M2.5 machine screw lengths Home Depot actually stocks, in mm. Shorter than
#: the M3 range because these are a specialty size for them — which is the
#: constraint, not the thread: they have the screws and the nuts, just not the
#: threaded standoffs an electronics shop would sell you.
STOCK_M25_SCREWS = (4, 5, 6, 10, 20)

#: A nut needs about its own height of thread through it to pull up tight.
MIN_NUT_ENGAGEMENT = 2.0

#: A flat steel corner brace, the thin kind sold four to a pack. Recorded here
#: not because the design uses one but because it must not: see
#: `Case.inner_face_clearance`.
BRACE_THICKNESS = 2.0


@dataclass(frozen=True)
class Stock:
    """Lumber and sheet goods, at their real (not nominal) sizes."""

    #: 1×3 pine: nominal 1×3, actually ¾ × 2½ in.
    frame_thickness: float = 0.75 * MM_PER_INCH
    frame_depth: float = 2.5 * MM_PER_INCH
    back_thickness: float = 0.25 * MM_PER_INCH
    acrylic_thickness: float = 0.093 * MM_PER_INCH
    #: Stock you can actually buy, for the "does it come out of one sheet" checks.
    board_length: float = 6 * 12 * MM_PER_INCH        # 6 ft of 1×3
    acrylic_sheet: tuple = (18 * MM_PER_INCH, 24 * MM_PER_INCH)
    plywood_sheet: tuple = (24 * MM_PER_INCH, 24 * MM_PER_INCH)


@dataclass(frozen=True)
class Electronics:
    """The stack that has to live in the cavity behind the panels."""

    pi_width: float = 85.0
    pi_length: float = 56.0
    #: A 1/4 in nylon spacer holding the Pi off the back board. Not a threaded
    #: standoff: the Pi's holes are plain through-holes, so the same screw-
    #: spacer-nut sandwich as the panels works, out of the same aisle.
    pi_standoff: float = 0.25 * MM_PER_INCH
    pi_pcb: float = 1.4
    #: Mounting holes on a Pi, all four of which want a spacer.
    pi_mount_holes: int = 4
    #: 2×20 stacking header. Present because the HAT fouls the Pi's port cans
    #: without it — that is what it is for, not a nicety.
    riser: float = 15.0
    hat_pcb: float = 1.6
    #: Tallest thing on the HAT, which is the screw terminal block.
    hat_components: float = 12.0

    @property
    def stack_depth(self):
        """Back board face to the top of the HAT."""
        return (self.pi_standoff + self.pi_pcb + self.riser
                + self.hat_pcb + self.hat_components)


@dataclass(frozen=True)
class Case:
    """The enclosure, derived from what goes in it."""

    panel: Panel = Panel()
    stock: Stock = Stock()
    electronics: Electronics = Electronics()
    panels_x: int = 2           # chained side by side
    panels_y: int = 1
    #: Gap between adjacent panels. MUST be zero. Pixels sit half a pitch in
    #: from each panel edge, so butted panels continue the grid across the seam
    #: at exactly one pitch; any gap at all shows as a dead stripe through the
    #: middle of every frame, and the kernel renders text straight across it.
    panel_gap: float = 0.0
    #: Slack between the panel block and the frame opening, per axis. The panels
    #: are located by their bolts, not by the opening, so this is deliberate:
    #: a snug wooden opening around a rigid PCB cracks the panel when the wood
    #: moves with the seasons.
    clearance: float = 3.0

    # --- the panel block --------------------------------------------------
    @property
    def panels_width(self):
        return (self.panel.width * self.panels_x
                + self.panel_gap * (self.panels_x - 1))

    @property
    def panels_height(self):
        return (self.panel.height * self.panels_y
                + self.panel_gap * (self.panels_y - 1))

    @property
    def canvas_pixels(self):
        """The pixel canvas the kernel renders to — 128 × 64 here."""
        return (self.panel.pixels_x * self.panels_x,
                self.panel.pixels_y * self.panels_y)

    # --- the frame --------------------------------------------------------
    @property
    def opening_width(self):
        return snap_up(self.panels_width + self.clearance)

    @property
    def opening_height(self):
        return snap_up(self.panels_height + self.clearance)

    @property
    def actual_clearance_x(self):
        """Slack the snapped opening really leaves, across the panels."""
        return self.opening_width - self.panels_width

    @property
    def actual_clearance_y(self):
        return self.opening_height - self.panels_height

    @property
    def inner_face_clearance(self):
        """Room for anything mounted on the frame's inner faces. There is none.

        The back assembly comes out backwards, which drags the panels through
        the whole depth of the cavity — so the inner faces are a swept volume,
        not spare surface. Whatever the clearance around the panels is, that is
        the total budget for brackets, blocks, wire clips and cable ties, and it
        is under 2 mm: less than the steel corner brace this build guide used to
        tell you to screw into all four corners.
        """
        return min(self.actual_clearance_x, self.actual_clearance_y) / 2

    @property
    def outer_width(self):
        return self.opening_width + 2 * self.stock.frame_thickness

    @property
    def outer_height(self):
        return self.opening_height + 2 * self.stock.frame_thickness

    @property
    def interior_depth(self):
        """Front of the frame to the back of it — the cavity."""
        return self.stock.frame_depth

    # --- cut list ---------------------------------------------------------
    @property
    def rail_length(self):
        """Top and bottom pieces, which run the full outer width."""
        return self.outer_width

    @property
    def stile_length(self):
        """Side pieces, which fit between the rails."""
        return self.outer_height - 2 * self.stock.frame_thickness

    @property
    def board_length_needed(self):
        return 2 * self.rail_length + 2 * self.stile_length

    # --- depth stack ------------------------------------------------------
    @property
    def standoff_ideal(self):
        """Back board to the back of a panel, so panel faces meet the acrylic.

        The panels hang off the back board rather than the frame, which is what
        lets the whole electronics assembly come out as one piece.

        This is the exact length, which is almost never a length anyone sells —
        see `standoff_length` for the one you actually buy.
        """
        return self.interior_depth - self.panel.depth

    @property
    def standoff_length(self):
        """The stock standoff to buy: the longest one that is not too long.

        Rounding DOWN rather than to-nearest is the whole point. Too long and
        the panels stand proud of the frame and press into the acrylic, which
        bows the face across its span and leaves the felt pads carrying a load
        they are not there for. Too short and the panels simply sit a little
        behind the acrylic instead of touching it, which costs nothing.
        """
        return self._standoff[0]

    @property
    def standoff_source(self):
        """What to actually buy for `standoff_length` — often two parts."""
        return self._standoff[1]

    @property
    def _standoff(self):
        fits = [s for s in STOCK_STANDOFFS if s[0] <= self.standoff_ideal]
        # Nothing short enough means the cavity is shallower than the shortest
        # spacer sold. Fall back to that shortest one rather than raising, so
        # `test_fit` still reports every other check instead of a traceback —
        # "the standoff fits the cavity" is the assertion that catches it.
        return max(fits) if fits else min(STOCK_STANDOFFS)

    @property
    def panel_recess(self):
        """How far the panel faces sit behind the acrylic, given stock parts."""
        return self.standoff_ideal - self.standoff_length

    # --- panel mounting screws --------------------------------------------
    @property
    def panel_screw_span(self):
        """What the screw crosses before it reaches the panel at all."""
        return self.stock.back_thickness + self.standoff_length

    @property
    def panel_screw_length(self):
        """Shortest stock M3 that engages the inlet without bottoming out.

        The panel's inlets are threaded and blind, which puts the screw between
        two walls: too short and it never bites, too long and it bottoms out on
        the end of the thread and stops before the joint is tight — which feels
        exactly like a tightened screw and holds nothing.
        """
        fits = [s for s in STOCK_M3_SCREWS
                if MIN_THREAD_ENGAGEMENT <= s - self.panel_screw_span
                <= self.panel.mount_thread_depth]
        # No length works — the check "a stock M3 screw fits the stack" says so.
        return min(fits) if fits else max(STOCK_M3_SCREWS)

    @property
    def panel_screw_engagement(self):
        """Thread actually engaged in the panel, with the chosen screw."""
        return self.panel_screw_length - self.panel_screw_span

    @property
    def pi_screw_span(self):
        """Back board, spacer and Pi board — everything the nut has to clear."""
        return (self.stock.back_thickness + self.electronics.pi_standoff
                + self.electronics.pi_pcb)

    @property
    def pi_screw_length(self):
        """Shortest stock M2.5 leaving enough thread proud to take a nut.

        Unlike the panel screws there is no bottoming out to worry about — the
        Pi's holes go straight through, so overshooting only leaves thread
        sticking up. Hence shortest-that-works rather than a window.
        """
        fits = [s for s in STOCK_M25_SCREWS
                if s >= self.pi_screw_span + MIN_NUT_ENGAGEMENT]
        # Nothing long enough: "a stock M2.5 screw clears the Pi stack" says so.
        return min(fits) if fits else max(STOCK_M25_SCREWS)

    #: How far apart the back board's perimeter screws sit. Wider than this and
    #: the thin plywood bows between them and daylight shows at the seam.
    back_screw_spacing: float = 4 * MM_PER_INCH

    @property
    def back_screws(self):
        """Screws holding the back board on — the count you undo for access.

        Worth deriving rather than eyeballing: this is the number that decides
        whether getting to the Pi is a two-minute job or a project, and the
        build sheet claimed eight against a 52 in perimeter.
        """
        perimeter = 2 * (self.outer_width + self.outer_height)
        return math.ceil(perimeter / self.back_screw_spacing)

    @property
    def spacers_needed(self):
        """Total spacer stacks, one per mounting hole across every panel."""
        return self.panel.mount_holes * self.panels_x * self.panels_y

    @property
    def cavity_behind_panels(self):
        """Depth available for the Pi/HAT stack."""
        return self.interior_depth - self.panel.depth

    @property
    def stack_headroom(self):
        """Slack left over once the Pi stack is in — cable room."""
        return self.cavity_behind_panels - self.electronics.stack_depth

    # --- reporting --------------------------------------------------------
    def cut_list(self):
        """(part, qty, dimensions) rows, ready to hand to the lumber desk."""
        return [
            ("Frame — top & bottom", 2, fraction(self.rail_length)),
            ("Frame — sides", 2, fraction(self.stile_length)),
            ("Back panel", 1,
             f"{fraction(self.outer_width)} × {fraction(self.outer_height)}"),
            ("Acrylic face", 1,
             f"{fraction(self.outer_width)} × {fraction(self.outer_height)}"),
        ]

    def report(self):
        lines = [
            "dizzyos sign enclosure — derived dimensions",
            "",
            f"  panel               {self.panel.width:.0f} × {self.panel.height:.0f} mm"
            f"  ({fraction(self.panel.width)} sq)",
            f"  panel block         {self.panels_width:.0f} × {self.panels_height:.0f} mm",
            f"  frame opening       {fraction(self.opening_width)} × {fraction(self.opening_height)}",
            f"  outside             {fraction(self.outer_width)} × {fraction(self.outer_height)}"
            f" × {fraction(self.interior_depth)} deep",
            f"  panel standoffs     {fraction(self.standoff_length)}"
            f"  — {self.standoff_source}",
            f"                      (exact would be {self.standoff_ideal:.1f} mm;"
            f" panel sits {self.panel_recess:.1f} mm behind the acrylic)",
            f"  spacer stacks       {self.spacers_needed}"
            f"  ({self.panel.mount_holes} per panel)",
            f"  panel screws        M3 × {self.panel_screw_length:.0f} mm"
            f"  ({self.panel_screw_engagement:.1f} mm into a"
            f" {self.panel.mount_thread_depth:.0f} mm inlet)",
            f"  Pi screws           M2.5 × {self.pi_screw_length:.0f} mm"
            f"  + nut, over a {fraction(self.electronics.pi_standoff)} spacer",
            f"  cavity behind       {self.cavity_behind_panels:.1f} mm",
            f"  Pi + riser + HAT    {self.electronics.stack_depth:.1f} mm",
            f"  headroom for cable  {self.stack_headroom:.1f} mm",
            f"  1×3 needed          {fraction(self.board_length_needed)}"
            f" of {fraction(self.stock.board_length)}",
            "",
            "cut list",
        ]
        for part, qty, size in self.cut_list():
            lines.append(f"  {qty} ×  {part:<24} {size}")
        return "\n".join(lines)


#: The case as actually being built.
CASE = Case()


if __name__ == "__main__":
    print(CASE.report())
