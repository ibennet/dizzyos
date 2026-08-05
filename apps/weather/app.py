"""Weather app — current conditions for a configured location.

Pulls a forecast from the free, keyless Open-Meteo API and renders a single static
frame: a procedurally-drawn weather icon (sun/cloud/rain/snow/storm...) and the
location on the left, and the current temperature, the day's high/low, and the local
time on the right. Falls back to a bundled snapshot if the API is unreachable, so the
sign is never blank.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from PIL import Image, ImageDraw

from kernel.app import App

# size = font pixel size; color = RGB.
PALETTE = {
    "temp": {"size": 30, "color": (245, 245, 245)},  # big current temperature
    "high": {"size": 11, "color": (255, 150, 90)},   # warm — daily high
    "low": {"size": 11, "color": (120, 200, 255)},   # cool — daily low
    "label": {"size": 10, "color": (170, 170, 170)}, # place name under the icon
    "time": {"size": 12, "color": (255, 196, 84)},   # amber clock
}
DIVIDER = (70, 70, 70)

# Shown if the API can't be reached on first paint. Mild, clear, offset 0 (UTC) so
# the clock still renders offline.
FALLBACK = {
    "utc_offset_seconds": 0,
    "current": {"temperature_2m": 70, "weather_code": 1, "is_day": 1},
    "daily": {"temperature_2m_max": [75], "temperature_2m_min": [60]},
}


def icon_category(code):
    """Map a WMO weather code to an icon category."""
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloudy"
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
    return "cloudy"


class WeatherApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._wx = None

    def refresh(self):
        base = self.config.get("api_base", "https://api.open-meteo.com/v1/forecast")
        params = {
            "latitude": self.config.get("latitude", 40.7128),
            "longitude": self.config.get("longitude", -74.0060),
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 1,
        }
        url = f"{base}?{urlencode(params)}"
        ttl = self.refresh_interval or 600
        self._wx = self.services.data.get_json(url, ttl=ttl, fallback=FALLBACK)

    # --- data extraction (pure, defensive against missing fields) ----------
    def _reading(self):
        wx = self._wx or FALLBACK
        current = wx.get("current") or {}
        daily = wx.get("daily") or {}
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []

        def rounded(value):
            return round(value) if isinstance(value, (int, float)) else None

        return {
            "temp": rounded(current.get("temperature_2m")),
            "high": rounded(highs[0]) if highs else None,
            "low": rounded(lows[0]) if lows else None,
            "code": current.get("weather_code", 3),
            "is_day": current.get("is_day", 1),
            "offset": wx.get("utc_offset_seconds", 0) or 0,
        }

    def _local_now(self, offset_seconds):
        """Current wall-clock time in the location's timezone."""
        return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)

    # --- rendering ---------------------------------------------------------
    def render(self, t):
        if self._wx is None:
            self.refresh()
        width, height = self.services.width, self.services.height
        reading = self._reading()

        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)

        # Header row: location on the left, local time on the right.
        header_font = self.services.fonts.get(PALETTE["label"]["size"])
        label = str(self.config.get("location_label", ""))
        if label:
            draw.text((2, 0), label, font=header_font, fill=PALETTE["label"]["color"])
        now = self._local_now(reading["offset"])  # recomputed each frame → ticks live
        time_str = now.strftime("%I:%M %p").lstrip("0")
        tw = int(header_font.getlength(time_str))
        draw.text((width - tw - 2, 0), time_str, font=header_font,
                  fill=PALETTE["time"]["color"])
        draw.line([(0, 12), (width, 12)], fill=DIVIDER)

        # Body left: the weather icon.
        category = icon_category(reading["code"])
        draw_icon(draw, 26, 37, category, reading["is_day"])

        # Body right: big current temperature.
        rx = 52
        temp_font = self.services.fonts.get(PALETTE["temp"]["size"])
        temp_str = f"{reading['temp']}°" if reading["temp"] is not None else "--°"
        draw.text((rx, 16), temp_str, font=temp_font, fill=PALETTE["temp"]["color"])

        # Body right: the day's high / low, as two color-coded segments.
        hl_y = 49
        high_font = self.services.fonts.get(PALETTE["high"]["size"])
        low_font = self.services.fonts.get(PALETTE["low"]["size"])
        high_str = f"H {reading['high']}°" if reading["high"] is not None else "H --"
        low_str = f"L {reading['low']}°" if reading["low"] is not None else "L --"
        draw.text((rx, hl_y), high_str, font=high_font, fill=PALETTE["high"]["color"])
        gap = int(high_font.getlength(high_str)) + 4
        draw.text((rx + gap, hl_y), low_str, font=low_font, fill=PALETTE["low"]["color"])

        return image


# --- procedural weather icons ---------------------------------------------
# All icons draw within roughly a 40x40 box centered on (cx, cy) using stock Pillow
# shapes, so there are no image assets to ship.

_SUN = (255, 200, 60)
_MOON = (200, 214, 235)
_CLOUD = (210, 210, 215)
_CLOUD_DARK = (120, 124, 132)
_RAIN = (90, 170, 255)
_SNOW = (225, 235, 245)
_BOLT = (255, 224, 70)


def draw_icon(draw, cx, cy, category, is_day):
    if category == "clear":
        if is_day:
            _sun(draw, cx, cy)
        else:
            _moon(draw, cx, cy)
    elif category == "partly":
        if is_day:
            _sun(draw, cx - 8, cy - 8, r=8, rays=False)
        else:
            _moon(draw, cx - 8, cy - 8, r=8)
        _cloud(draw, cx + 2, cy + 4)
    elif category == "fog":
        _cloud(draw, cx, cy - 2)
        for i in range(3):
            y = cy + 10 + i * 4
            draw.line([(cx - 15, y), (cx + 15, y)], fill=(150, 150, 155))
    elif category in ("drizzle", "rain"):
        _cloud(draw, cx, cy - 4)
        drops = 3 if category == "drizzle" else 4
        for i in range(drops):
            x = cx - 12 + i * 8
            draw.line([(x, cy + 10), (x - 3, cy + 17)], fill=_RAIN)
    elif category == "snow":
        _cloud(draw, cx, cy - 4)
        for i in range(4):
            x = cx - 12 + i * 8
            draw.ellipse([x - 1, cy + 11, x + 1, cy + 13], fill=_SNOW)
    elif category == "storm":
        _cloud(draw, cx, cy - 4, color=_CLOUD_DARK)
        draw.polygon(
            [(cx, cy + 9), (cx - 5, cy + 17), (cx - 1, cy + 17),
             (cx - 3, cy + 22), (cx + 5, cy + 13), (cx + 1, cy + 13)],
            fill=_BOLT,
        )
    else:  # cloudy
        _cloud(draw, cx, cy)


def _sun(draw, cx, cy, r=10, rays=True):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_SUN)
    if not rays:
        return
    for i in range(8):
        # 8 rays at 45-degree steps, approximated with integer offsets.
        dx, dy = _RAY_DIRS[i]
        x0, y0 = cx + dx * (r + 3), cy + dy * (r + 3)
        x1, y1 = cx + dx * (r + 7), cy + dy * (r + 7)
        draw.line([(x0, y0), (x1, y1)], fill=_SUN)


# Unit-ish direction vectors for the 8 sun rays (diagonals scaled to ~0.7).
_RAY_DIRS = [
    (1, 0), (0, 1), (-1, 0), (0, -1),
    (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7),
]


def _moon(draw, cx, cy, r=10):
    # Crescent: a lit disc with an offset black disc bitten out of it.
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_MOON)
    draw.ellipse([cx - r + 5, cy - r, cx + r + 5, cy + r], fill="black")


def _cloud(draw, cx, cy, color=_CLOUD):
    # Three overlapping puffs with a flat base.
    draw.ellipse([cx - 15, cy - 2, cx - 3, cy + 10], fill=color)
    draw.ellipse([cx - 6, cy - 8, cx + 8, cy + 8], fill=color)
    draw.ellipse([cx + 3, cy - 2, cx + 15, cy + 10], fill=color)
    draw.rectangle([cx - 13, cy + 4, cx + 13, cy + 10], fill=color)
