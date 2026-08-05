"""Cafe Menu app — Izzy's Cafe menu on the panel.

Pulls structured menu JSON from the website's `/izzys-cafe.json` feed (so the site
stays the single source of truth) and renders the whole menu statically — no scroll —
in two columns. Each column is sized to its own content, so the short-item column
stays narrow and the long-item column ("Cardamom cold foam") gets the width it needs.
Prefers a crisp bitmap font; falls back to a bundled copy of the menu if the feed is
unreachable, so the sign is never blank.
"""

from PIL import Image, ImageDraw

from kernel.app import App
from kernel.render import glyph_height

COLORS = {
    "title": (255, 196, 84),
    "heading": (120, 200, 255),
    "item": (232, 232, 232),
}

FALLBACK_MENU = {
    "title": "Izzy's Cafe",
    "sections": [
        {"heading": "Drinks", "items": ["Coffee", "Matcha"]},
        {"heading": "Milks", "items": ["Whole", "Oat"]},
        {"heading": "Syrups", "items": ["Cardamom", "Earl Gray"]},
        {"heading": "Food", "items": ["Cardamom cookie", "Ricotta toast"]},
    ],
}


class CafeMenuApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._menu = None

    def refresh(self):
        url = self.config.get("menu_url")
        ttl = self.refresh_interval or 300
        if url:
            self._menu = self.services.data.get_json(url, ttl=ttl, fallback=FALLBACK_MENU)
        else:
            self._menu = FALLBACK_MENU

    # --- layout ------------------------------------------------------------
    @staticmethod
    def _section_lines(sections):
        """Flatten sections into (text, kind) lines: a heading then its items."""
        lines = []
        for section in sections:
            lines.append((section.get("heading", "").upper(), "heading"))
            for item in section.get("items", []):
                lines.append((item, "item"))
        return lines

    @staticmethod
    def _split_columns(sections):
        """Split sections across two columns, keeping order and balancing height."""
        total = sum(1 + len(s.get("items", [])) for s in sections)
        half = (total + 1) // 2
        left, right, filled = [], [], 0
        for section in sections:
            if filled < half:
                left.append(section)
                filled += 1 + len(section.get("items", []))
            else:
                right.append(section)
        return left, right

    def _choose_font(self, title, left, right, width, height):
        """Pick the largest bundled bitmap font that fits; else auto-size a TTF."""
        fonts = self.services.fonts
        rows = max(len(left), len(right), 1)

        explicit = self.config.get("font")
        bitmaps = [fonts.bitmap(explicit)] if explicit else fonts.bitmaps()
        bitmaps = [font for font in bitmaps if font is not None]
        for font in bitmaps:  # largest-first
            fit = self._fits(font, title, left, right, rows, width, height)
            if fit:
                return fit
        if bitmaps:  # none fit cleanly — use the smallest, content may clip
            font = bitmaps[-1]
            gh, top = glyph_height(font)
            return font, gh + 1, gh, top

        # No bitmap fonts bundled: auto-size a scalable font (equal columns).
        col_w = (width - 3) // 2
        for size in range(11, 4, -1):
            font = fonts.get(size)
            gh, top = glyph_height(font)
            if (gh + 2) + rows * (gh + 1) > height:
                continue
            if font.getlength(title) > width:
                continue
            if any(font.getlength(text) > col_w for text, _ in left + right):
                continue
            return font, gh + 1, gh, top
        font = fonts.get(5)
        gh, top = glyph_height(font)
        return font, gh + 1, gh, top

    @staticmethod
    def _fits(font, title, left, right, rows, width, height):
        """Return (font, row_h, title_h, top) if everything fits, else None."""
        gh, top = glyph_height(font)
        row_h = gh + 1
        if (gh + 2) + rows * row_h > height:
            return None
        if font.getlength(title) > width:
            return None
        left_w = max((font.getlength(text) for text, _ in left), default=0)
        right_w = max((font.getlength(text) for text, _ in right), default=0)
        if left_w + right_w + 3 > width:
            return None
        return font, row_h, gh, top

    def render(self, t):  # t unused: the layout is static
        if self._menu is None:
            self.refresh()
        width, height = self.services.width, self.services.height
        menu = self._menu or FALLBACK_MENU
        title = menu.get("title", "Menu")

        left_secs, right_secs = self._split_columns(menu.get("sections", []))
        left = self._section_lines(left_secs)
        right = self._section_lines(right_secs)

        font, row_h, title_h, top = self._choose_font(title, left, right, width, height)

        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)

        # Centered title + divider.
        title_w = draw.textlength(title, font=font)
        draw.text(((width - title_w) // 2, -top), title, font=font, fill=COLORS["title"])
        body_top = title_h + 2
        draw.line([(0, body_top - 1), (width, body_top - 1)], fill=(70, 70, 70))

        # Columns sized to their own content; the leftover space becomes the gutter.
        left_w = max((font.getlength(text) for text, _ in left), default=0)
        right_w = max((font.getlength(text) for text, _ in right), default=0)
        gutter = max(3, min(int(width - left_w - right_w), 12))
        right_x = int(left_w) + gutter

        for lines, x0 in ((left, 0), (right, right_x)):
            y = body_top + 1
            for text, kind in lines:
                draw.text((x0, y - top), text, font=font, fill=COLORS[kind])
                y += row_h

        return image
