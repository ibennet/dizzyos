"""Cafe Menu app — Izzy's Cafe menu on the panel.

Pulls structured menu JSON from the website's `/izzys-cafe.json` feed (so the site
stays the single source of truth) and renders it with a fixed title header above a
vertically scrolling body. Falls back to a bundled copy if the feed is unreachable,
so the sign is never blank.
"""

from PIL import Image, ImageDraw

from kernel.app import App
from kernel.render import render_lines

# size = font pixel size; color = RGB.
PALETTE = {
    "title": {"size": 13, "color": (255, 196, 84)},
    "heading": {"size": 12, "color": (120, 200, 255)},
    "item": {"size": 11, "color": (232, 232, 232)},
    "spacer": {"size": 4, "color": (0, 0, 0)},
}

# Shown if the feed can't be reached on first paint. Mirrors the site content.
FALLBACK_MENU = {
    "title": "Izzy's Cafe",
    "sections": [
        {"heading": "Drinks", "items": ["Latte", "Matcha Latte", "Americano", "Pourover"]},
        {"heading": "Milks", "items": ["Whole", "Oat"]},
        {"heading": "Additions", "items": ["Cardamom syrup", "Cardamom cold foam", "Ice"]},
        {"heading": "Food", "items": ["Cardamom cookie", "Ricotta toast"]},
    ],
}


class CafeMenuApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._menu = None
        self._content = None  # cached tall body image; rebuilt after each refresh

    def refresh(self):
        url = self.config.get("menu_url")
        ttl = self.refresh_interval or 300
        if url:
            self._menu = self.services.data.get_json(url, ttl=ttl, fallback=FALLBACK_MENU)
        else:
            self._menu = FALLBACK_MENU
        self._content = None

    def _build_body(self, width):
        menu = self._menu or FALLBACK_MENU
        lines = []
        for section in menu.get("sections", []):
            lines.append((section.get("heading", "").upper(), "heading"))
            for item in section.get("items", []):
                lines.append((item, "item"))
            lines.append(("", "spacer"))
        self._content = render_lines(
            lines, self.services.fonts, width, PALETTE, line_gap=1, pad=2
        )

    def render(self, t):
        if self._menu is None:
            self.refresh()
        width, height = self.services.width, self.services.height
        menu = self._menu or FALLBACK_MENU

        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)

        # Fixed title header.
        title_font = self.services.fonts.get(PALETTE["title"]["size"])
        draw.text((2, 0), menu.get("title", "Menu"), font=title_font,
                  fill=PALETTE["title"]["color"])
        ascent, descent = title_font.getmetrics()
        header_h = ascent + descent + 1
        draw.line([(0, header_h), (width, header_h)], fill=(70, 70, 70))

        # Scrolling body below the header.
        top = header_h + 2
        view_h = max(height - top, 1)
        if self._content is None:
            self._build_body(width)
        content = self._content

        body = Image.new("RGB", (width, view_h), "black")
        if content.height > view_h:
            speed = self.config.get("scroll_speed", 14)  # pixels/second
            period = content.height + view_h  # trailing gap before the loop repeats
            offset = int((t * speed) % period)
            body.paste(content, (0, -offset))
            body.paste(content, (0, -offset + period))
        else:
            body.paste(content, (0, 0))
        image.paste(body, (0, top))

        return image
