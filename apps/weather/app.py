"""Weather app — current conditions for a configured location.

Pulls a forecast from the free, keyless Open-Meteo API and renders a single static
frame: a procedurally-drawn weather icon (sun/cloud/rain/snow/storm...) and the
location on the left, and the current temperature, the day's high/low, and the local
time on the right. Text uses the kernel's hand-designed bitmap font (services.fonts.pixel())
so it stays crisp on the LED panel instead of smearing like an anti-aliased TTF.
Falls back to a bundled snapshot if the API is unreachable, so the sign is never blank.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from PIL import ImageDraw

from kernel.app import App

from apps.weather.icons import category, draw_icon

# scale = bitmap-font pixel scale (1 = 5x7, 3 = 15x21...); color = RGB.
PALETTE = {
    "temp": {"scale": 4, "color": (245, 245, 245)},  # big current temp (shrinks to fit)
    "high": {"scale": 1, "color": (255, 150, 90)},   # warm — daily high
    "low": {"scale": 1, "color": (120, 200, 255)},   # cool — daily low
    "label": {"scale": 1, "color": (170, 170, 170)}, # place name in the header
    "time": {"scale": 1, "color": (255, 196, 84)},   # amber clock in the header
    "rain": {"scale": 1, "color": (120, 190, 255)},  # upcoming-rain alert (bottom)
}

# Shown if the API can't be reached on first paint. Mild, clear, offset 0 (UTC) so
# the clock still renders offline.
FALLBACK = {
    "utc_offset_seconds": 0,
    "current": {"temperature_2m": 70, "weather_code": 1, "is_day": 1},
    "daily": {"temperature_2m_max": [75], "temperature_2m_min": [60]},
}


def _ellipsize(font, text, scale, max_w):
    """Trim `text` (appending an ellipsis) until it fits within `max_w` pixels."""
    if not text or font.measure(text, scale) <= max_w:
        return text
    while text and font.measure(text + "…", scale) > max_w:
        text = text[:-1]
    return text + "…" if text else ""


class WeatherApp(App):
    def on_start(self, services):
        super().on_start(services)
        self._wx = None
        self._label = self.config.get("location_label") or ""

    def refresh(self):
        lat, lon, self._label = self._resolve_location()
        base = self.config.get("api_base", "https://api.open-meteo.com/v1/forecast")
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,is_day",
            "hourly": "precipitation_probability",
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 2,  # 48h of hourly, so "next rain" is found even late in the day
        }
        url = f"{base}?{urlencode(params)}"
        ttl = self.refresh_interval or 600
        self._wx = self.services.data.get_json(url, ttl=ttl, fallback=FALLBACK)

    def _resolve_location(self):
        """Return (lat, lon, label). Geocode `zipcode` via zippopotam.us when set,
        else use the configured lat/lon. Never raises — degrades to the fallback
        coordinates on any lookup failure so the sign always renders."""
        lat = self.config.get("latitude", 40.7128)
        lon = self.config.get("longitude", -74.0060)
        label = self.config.get("location_label") or ""
        zipc = str(self.config.get("zipcode") or "").strip()
        if not zipc:
            return lat, lon, label

        country = str(self.config.get("country") or "us").strip() or "us"
        base = self.config.get("geocode_base", "https://api.zippopotam.us")
        # ZIP -> coordinates is static, so cache it hard; {} fallback degrades to lat/lon.
        geo = self.services.data.get_json(f"{base}/{country}/{zipc}", ttl=2592000, fallback={})
        places = geo.get("places") if isinstance(geo, dict) else None
        if places:
            place = places[0]
            try:
                lat, lon = float(place["latitude"]), float(place["longitude"])
            except (KeyError, TypeError, ValueError):
                return lat, lon, label  # keep configured coords on malformed data
            if not label:
                label = place.get("place name", "").strip() or zipc
        return lat, lon, label

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
        """Current wall-clock time in the location's timezone, as a naive datetime
        (matches the API's local, tz-naive hourly timestamps under timezone=auto)."""
        return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).replace(tzinfo=None)

    def _next_rain(self, now):
        """Local 'HH:MM' (24-hour) of the next hour within 24h whose precipitation
        probability meets the threshold, or None if none does. `now` is naive local."""
        wx = self._wx or {}
        hourly = wx.get("hourly") or {}
        times = hourly.get("time") or []
        probs = hourly.get("precipitation_probability") or []
        threshold = self.config.get("rain_probability_threshold", 30)
        horizon = now + timedelta(hours=24)
        for tstr, prob in zip(times, probs):
            if not isinstance(prob, (int, float)) or prob < threshold:
                continue
            try:
                t = datetime.fromisoformat(tstr)
            except (ValueError, TypeError):
                continue
            if now <= t <= horizon:
                return t.strftime("%H:%M")
        return None

    # --- rendering ---------------------------------------------------------
    def render(self, t):
        # Data is primed by the launcher's off-loop refresh(); until then _reading()
        # falls back to FALLBACK, so we never fetch on the render thread.
        width = self.services.width
        pf = self.services.fonts.pixel()
        reading = self._reading()

        image = self.blank()
        draw = ImageDraw.Draw(image)

        # Header row: local time on the right, location on the left — the label is
        # clipped to the clock's left edge so a long place name can't run into it.
        now = self._local_now(reading["offset"])  # recomputed each frame → ticks live
        time_str = now.strftime("%I:%M %p").lstrip("0")
        ts = PALETTE["time"]["scale"]
        time_x = width - pf.measure(time_str, ts) - 2
        pf.draw_text(draw, time_x, 2, time_str, PALETTE["time"]["color"], scale=ts)

        ls = PALETTE["label"]["scale"]
        label = _ellipsize(pf, self._label, ls, time_x - 4)
        if label:
            pf.draw_text(draw, 2, 2, label, PALETTE["label"]["color"], scale=ls)

        # Body left: the weather icon.
        draw_icon(draw, 26, 33, category(reading["code"]), reading["is_day"])

        # Body right: big current temperature. Shrink a size if 3 digits won't fit.
        rx = 52
        temp_str = f"{reading['temp']}°" if reading["temp"] is not None else "--°"
        cs = PALETTE["temp"]["scale"]
        while cs > 1 and rx + pf.measure(temp_str, cs) > width - 2:
            cs -= 1
        pf.draw_text(draw, rx, 13, temp_str, PALETTE["temp"]["color"], scale=cs)

        # Next likely rain within 24h — only when the chance meets the threshold.
        rain_at = self._next_rain(now)

        # Body right: the day's high / low, as two color-coded segments. Drop the
        # degree glyphs if the pretty form would overflow (3-digit or sub-zero temps).
        hl_y = 46
        hs = PALETTE["high"]["scale"]
        for deg in ("°", ""):
            high_str = f"H {reading['high']}{deg}" if reading["high"] is not None else "H --"
            low_str = f"L {reading['low']}{deg}" if reading["low"] is not None else "L --"
            gap = pf.measure(high_str, hs) + 4
            if rx + gap + pf.measure(low_str, hs) <= width - 2:
                break
        pf.draw_text(draw, rx, hl_y, high_str, PALETTE["high"]["color"], scale=hs)
        pf.draw_text(draw, rx + gap, hl_y, low_str, PALETTE["low"]["color"], scale=hs)

        if rain_at:
            pf.draw_text(draw, 2, 57, f"Rain at {rain_at}", PALETTE["rain"]["color"],
                         scale=PALETTE["rain"]["scale"])

        return image
