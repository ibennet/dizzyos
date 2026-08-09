"""Order app — "order now!" pointing at a scannable QR code.

The left half is the pitch: a "welcome to izzy's cafe!" greeting over bobbing
lowercase "order now!" in a warm pulsing color, a few twinkling sparkles, and
a marching-dash arrow leading the eye to
the right half: a Version 3 QR code (1 LED px per module, full quiet zone —
the density validated on hardware by the qr_test app) that opens the online
order page. The QR frame is rendered once and never animated; a code that
moves is a code that doesn't scan.

Dark modules are unlit (decoders want dark-on-light) and light modules render
at `white_level`, so bloom can be tuned without touching panel brightness.
"""

import math

from PIL import ImageDraw

from kernel.app import App

QUIET = 4  # quiet-zone width in modules, per the QR spec
QR_VERSION = 3  # 29x29 modules; the order URL overflows V2 at EC M, V3 has room
QR_EC = "M"

TEXT_SCALE = 2
AMBER = (255, 180, 70)
CORAL = (255, 120, 130)
CREAM = (235, 225, 195)
ARROW = (120, 220, 160)
SPARKLE = (255, 240, 190)

WELCOME = ("welcome to", "izzy's cafe!")

# (x, y, phase) — hand-placed twinkles around the text block.
SPARKLES = [(3, 24, 0.0), (80, 4, 2.1), (66, 56, 4.2), (4, 58, 1.3), (78, 24, 3.4)]


def _matrix(payload, version, ec_level):
    """Return the QR module grid (True = dark) including the quiet zone."""
    import qrcode

    qr = qrcode.QRCode(
        version=version,
        error_correction=getattr(qrcode.constants, f"ERROR_CORRECT_{ec_level}"),
        border=QUIET,
    )
    qr.add_data(payload)
    qr.make(fit=False)  # raise instead of silently growing past the panel
    return qr.get_matrix()


def _lerp(a, b, k):
    return tuple(round(ca + (cb - ca) * k) for ca, cb in zip(a, b))


class OrderApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._base = None
        self._error = None
        try:
            self._qr = _matrix(self.config.get("url", ""), QR_VERSION, QR_EC)
        except ImportError:
            self._error = "pip install qrcode"
        except Exception as exc:  # DataOverflowError: payload too long for V3
            self._error = str(exc)

    # --- static layer -------------------------------------------------------
    def _draw_code(self, image, matrix, scale, x, y, white):
        px = image.load()
        for row, cells in enumerate(matrix):
            for col, dark in enumerate(cells):
                if dark:
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px[x + col * scale + dx, y + row * scale + dy] = white

    def _build_base(self):
        frame = self.blank()
        if self._error:
            draw = ImageDraw.Draw(frame)
            pf = self.services.fonts.pixel()
            pf.draw_text(draw, 2, 2, "order", (255, 120, 120))
            pf.draw_text(draw, 2, 12, self._error[:40], (200, 200, 200))
            return frame

        level = int(self.config.get("white_level", 255))
        scale = int(self.config.get("scale", 1))
        size = len(self._qr) * scale
        self._qr_x = frame.width - size - 2
        self._qr_y = (frame.height - size) // 2
        self._draw_code(frame, self._qr, scale, self._qr_x, self._qr_y, (level,) * 3)
        return frame

    # --- animated layer -----------------------------------------------------
    def _draw_text(self, draw, pf, t):
        mid = self._qr_x // 2  # center of the region left of the QR
        for i, line in enumerate(WELCOME):
            pf.draw_text(draw, mid - pf.measure(line) // 2, 3 + i * 9, line, CREAM)
        bob = round(1.5 * math.sin(t * 2.2))
        color = _lerp(AMBER, CORAL, (math.sin(t * 1.7) + 1) / 2)
        w_order = pf.measure("order", TEXT_SCALE)
        w_now = pf.measure("now!", TEXT_SCALE)
        pf.draw_text(draw, 6, 26 + bob, "order", color, TEXT_SCALE)
        pf.draw_text(draw, 6 + (w_order - w_now) // 2, 44 + bob, "now!", color, TEXT_SCALE)

    def _draw_arrow(self, draw, t):
        qr_mid = self._qr_y + (len(self._qr) * int(self.config.get("scale", 1))) // 2
        text_mid = 42  # vertical center of the "order now!" block
        tip = self._qr_x - 4
        # Elbow: out from the text, up to the QR's center line, then in.
        path = [(66, text_mid), (tip - 7, text_mid), (tip - 7, qr_mid), (tip - 2, qr_mid)]
        phase = int(t * 12) % 6
        d = 0
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
            for i in range(abs(x1 - x0) + abs(y1 - y0)):
                if (d - phase) % 6 < 3:  # marching dashes, 3 on / 3 off
                    draw.point((x0 + sx * i, y0 + sy * i), fill=ARROW)
                d += 1
        draw.polygon([(tip - 3, qr_mid - 3), (tip, qr_mid), (tip - 3, qr_mid + 3)], fill=ARROW)

    def _draw_sparkles(self, draw, t):
        for x, y, phase in SPARKLES:
            k = (math.sin(t * 3.0 + phase) + 1) / 2
            if k < 0.35:  # off most of the trough — a twinkle, not a lamp
                continue
            draw.point((x, y), fill=_lerp((60, 50, 30), SPARKLE, k))
            if k > 0.85:  # brief 4-point flare at the peak
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    draw.point((x + dx, y + dy), fill=_lerp((60, 50, 30), SPARKLE, k - 0.5))

    def render(self, t):
        if self._base is None:
            self._base = self._build_base()
        frame = self._base.copy()
        if self._error:
            return frame
        draw = ImageDraw.Draw(frame)
        pf = self.services.fonts.pixel()
        self._draw_text(draw, pf, t)
        self._draw_arrow(draw, t)
        self._draw_sparkles(draw, t)
        return frame
