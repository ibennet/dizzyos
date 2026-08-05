"""Weather app — current conditions for a configured location.

Pulls a forecast from the free, keyless Open-Meteo API and renders a single static
frame: a procedurally-drawn weather icon (sun/cloud/rain/snow/storm...) and the
location on the left, and the current temperature, the day's high/low, and the local
time on the right. Falls back to a bundled snapshot if the API is unreachable, so the
sign is never blank.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from PIL import ImageDraw

from kernel.app import App
from kernel.render import text_width

from apps.weather.icons import category, draw_icon

# size = font pixel size; color = RGB.
PALETTE = {
    "temp": {"size": 30, "color": (245, 245, 245)},  # big current temperature
    "high": {"size": 11, "color": (255, 150, 90)},   # warm — daily high
    "low": {"size": 11, "color": (120, 200, 255)},   # cool — daily low
    "label": {"size": 10, "color": (170, 170, 170)}, # place name in the header
    "time": {"size": 10, "color": (255, 196, 84)},   # amber clock in the header
}
DIVIDER = (70, 70, 70)

# Shown if the API can't be reached on first paint. Mild, clear, offset 0 (UTC) so
# the clock still renders offline.
FALLBACK = {
    "utc_offset_seconds": 0,
    "current": {"temperature_2m": 70, "weather_code": 1, "is_day": 1},
    "daily": {"temperature_2m_max": [75], "temperature_2m_min": [60]},
}


def _ellipsize(text, font, max_w):
    """Trim `text` (appending an ellipsis) until it fits within `max_w` pixels."""
    if not text or text_width(font, text) <= max_w:
        return text
    while text and text_width(font, text + "…") > max_w:
        text = text[:-1]
    return text + "…" if text else ""


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
        # Data is primed by the launcher's off-loop refresh(); until then _reading()
        # falls back to FALLBACK, so we never fetch on the render thread.
        width = self.services.width
        reading = self._reading()

        image = self.blank()
        draw = ImageDraw.Draw(image)

        # Header row: local time on the right, location on the left — the label is
        # clipped to the clock's left edge so a long place name can't run into it.
        now = self._local_now(reading["offset"])  # recomputed each frame → ticks live
        time_str = now.strftime("%I:%M %p").lstrip("0")
        time_font = self.services.fonts.get(PALETTE["time"]["size"])
        time_x = width - text_width(time_font, time_str) - 2
        draw.text((time_x, 0), time_str, font=time_font, fill=PALETTE["time"]["color"])

        label_font = self.services.fonts.get(PALETTE["label"]["size"])
        label = _ellipsize(str(self.config.get("location_label", "")), label_font, time_x - 4)
        if label:
            draw.text((2, 0), label, font=label_font, fill=PALETTE["label"]["color"])
        draw.line([(0, 12), (width, 12)], fill=DIVIDER)

        # Body left: the weather icon.
        draw_icon(draw, 26, 37, category(reading["code"]), reading["is_day"])

        # Body right: big current temperature.
        rx = 52
        temp_font = self.services.fonts.get(PALETTE["temp"]["size"])
        temp_str = f"{reading['temp']}°" if reading["temp"] is not None else "--°"
        draw.text((rx, 16), temp_str, font=temp_font, fill=PALETTE["temp"]["color"])

        # Body right: the day's high / low, as two color-coded segments. Drop the
        # degree glyphs if the pretty form would overflow (3-digit or sub-zero temps).
        hl_y = 49
        high_font = self.services.fonts.get(PALETTE["high"]["size"])
        low_font = self.services.fonts.get(PALETTE["low"]["size"])
        for deg in ("°", ""):
            high_str = f"H {reading['high']}{deg}" if reading["high"] is not None else "H --"
            low_str = f"L {reading['low']}{deg}" if reading["low"] is not None else "L --"
            gap = text_width(high_font, high_str) + 4
            if rx + gap + text_width(low_font, low_str) <= width - 2:
                break
        draw.text((rx, hl_y), high_str, font=high_font, fill=PALETTE["high"]["color"])
        draw.text((rx + gap, hl_y), low_str, font=low_font, fill=PALETTE["low"]["color"])

        return image
