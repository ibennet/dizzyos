"""Rendering helpers shared by apps: fonts and text measuring.

Font priority for legible text on a 64px panel:
1. A bundled **bitmap** font (`.bdf`) — rendered 1:1 with NO antialiasing, exactly
   how a real LED sign looks. This is the crispest option at tiny sizes. BDFs are
   compiled once to Pillow's binary font format and cached under `fonts/.cache/`.
2. A bundled scalable font (`.ttf`/`.otf`).
3. A system monospace, then Pillow's built-in font — so the sign always renders.
"""

import glob
import os

from PIL import BdfFontFile, ImageFont

_SYSTEM_FONTS = [
    "/System/Library/Fonts/Menlo.ttc",  # macOS
    "/System/Library/Fonts/SFNSMono.ttf",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Raspberry Pi OS / Debian
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class FontBook:
    """Loads and caches fonts from `font_dir` (bitmap + scalable), then the system."""

    def __init__(self, font_dir):
        self.font_dir = font_dir
        self._ttf = sorted(
            glob.glob(os.path.join(font_dir, "*.ttf"))
            + glob.glob(os.path.join(font_dir, "*.otf"))
        )
        self._bdf = sorted(glob.glob(os.path.join(font_dir, "*.bdf")))
        self._ttf_cache = {}
        self._bitmap_cache = {}

    def bitmap(self, name=None):
        """Return a crisp fixed-size bitmap font (or None if no `.bdf` is bundled).

        `name` selects a specific file (e.g. "4x6.bdf"); otherwise the first BDF by
        name is used (the smallest, given the "WxH" naming convention).
        """
        key = name or "__default__"
        if key not in self._bitmap_cache:
            self._bitmap_cache[key] = self._load_bitmap(name)
        return self._bitmap_cache[key]

    def get(self, size):
        """Return a scalable font at `size` (bundled TTF, system, or built-in)."""
        if size not in self._ttf_cache:
            self._ttf_cache[size] = self._load_ttf(size)
        return self._ttf_cache[size]

    # ------------------------------------------------------------------
    def _load_bitmap(self, name):
        if not self._bdf:
            return None
        if name:
            path = next((p for p in self._bdf if os.path.basename(p) == name), None)
        else:
            path = self._bdf[0]
        if not path:
            return None

        cache_dir = os.path.join(self.font_dir, ".cache")
        os.makedirs(cache_dir, exist_ok=True)
        prefix = os.path.join(cache_dir, os.path.splitext(os.path.basename(path))[0])
        pil_path = prefix + ".pil"
        if not os.path.exists(pil_path):
            with open(path, "rb") as handle:
                BdfFontFile.BdfFontFile(handle).save(prefix)
        return ImageFont.load(pil_path)

    def _load_ttf(self, size):
        for path in self._ttf + _SYSTEM_FONTS:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # older Pillow: fixed-size default
            return ImageFont.load_default()


def text_width(font, text):
    """Pixel width of `text` in `font`."""
    return int(font.getlength(text))


def glyph_height(font, sample="Aghpqy1|"):
    """True ink height (px) of a font, spanning ascenders and descenders."""
    box = font.getbbox(sample)
    return box[3] - box[1], box[1]  # (height, top_bearing)
