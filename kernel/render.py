"""Rendering helpers shared by apps: fonts, text measuring, scroll math.

At 64px panel height a crisp bitmap/pixel font reads best. Drop a `.ttf`/`.otf`
(e.g. Pixel Operator, Cozette, Press Start 2P) into `fonts/` and it's picked up
automatically. If no bundled font is found we fall back to a system monospace, then
to Pillow's built-in font, so the sign always renders something legible.
"""

import glob
import os

from PIL import Image, ImageDraw, ImageFont

# System monospace fonts to try when no font is bundled in fonts/.
_SYSTEM_FONTS = [
    "/System/Library/Fonts/Menlo.ttc",  # macOS
    "/System/Library/Fonts/SFNSMono.ttf",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Raspberry Pi OS / Debian
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class FontBook:
    """Loads and caches fonts by pixel size from `font_dir`, then the system."""

    def __init__(self, font_dir):
        self.font_dir = font_dir
        self._bundled = sorted(
            glob.glob(os.path.join(font_dir, "*.ttf"))
            + glob.glob(os.path.join(font_dir, "*.otf"))
        )
        self._cache = {}

    def get(self, size):
        if size not in self._cache:
            self._cache[size] = self._load(size)
        return self._cache[size]

    def _load(self, size):
        for path in self._bundled + _SYSTEM_FONTS:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        # Last resort: Pillow's built-in font (scalable on Pillow >= 10.1).
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # older Pillow: fixed-size default
            return ImageFont.load_default()


def text_width(font, text):
    """Pixel width of `text` in `font`."""
    return int(font.getlength(text))


def render_lines(lines, font_book, width, palette, line_gap=1, pad=1):
    """Render a vertical stack of styled lines into a tall RGB image.

    `lines` is a list of (text, kind) where `kind` keys into `palette` for color
    and font size. Returns an image `width` wide and as tall as the content needs
    — ready to be scrolled through a smaller viewport.
    """
    laid_out, y = [], pad
    for text, kind in lines:
        style = palette[kind]
        font = font_book.get(style["size"])
        ascent, descent = font.getmetrics()
        laid_out.append((text, font, style["color"], y))
        y += ascent + descent + line_gap
    total_height = max(y - line_gap + pad, 1)

    image = Image.new("RGB", (width, total_height), "black")
    draw = ImageDraw.Draw(image)
    for text, font, color, top in laid_out:
        draw.text((pad, top), text, font=font, fill=color)
    return image
