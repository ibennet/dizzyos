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

from dataclasses import dataclass

MM_PER_INCH = 25.4


def inches(mm):
    """Millimetres as a decimal-inch float."""
    return mm / MM_PER_INCH


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

    @property
    def width(self):
        return self.pixels_x * self.pitch

    @property
    def height(self):
        return self.pixels_y * self.pitch


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
    #: M2.5 standoffs holding the Pi off the back board.
    pi_standoff: float = 6.0
    pi_pcb: float = 1.4
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
    #: Total slack between the panel block and the frame opening, per axis.
    #: Small enough that the panels cannot wander, large enough to absorb a
    #: saw kerf and seasonal movement in the wood.
    clearance: float = 3.0

    # --- the panel block --------------------------------------------------
    @property
    def panels_width(self):
        return self.panel.width * self.panels_x

    @property
    def panels_height(self):
        return self.panel.height * self.panels_y

    # --- the frame --------------------------------------------------------
    @property
    def opening_width(self):
        return self.panels_width + self.clearance

    @property
    def opening_height(self):
        return self.panels_height + self.clearance

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
    def standoff_length(self):
        """Back board to the back of a panel, so panel faces meet the acrylic.

        The panels hang off the back board rather than the frame, which is what
        lets the whole electronics assembly come out as one piece.
        """
        return self.interior_depth - self.panel.depth

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
            f"  panel standoffs     {self.standoff_length:.1f} mm ({fraction(self.standoff_length)})",
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
