"""Cached data fetching, shared by all apps.

The render loop must never block on the network, so apps fetch through this service
from `refresh()` (which the launcher calls off the hot path) and read the cached
result in `render()`. Fetches are cached with a TTL and are stale-on-error: if a
refresh fails, the last good value keeps showing instead of a blank screen.

Supports http(s) URLs and local file paths (handy for offline dev against a
locally-built `izzys-cafe.json`). Uses only the standard library.
"""

import json
import threading
import time
import urllib.request


class DataService:
    def __init__(self, clock=time.monotonic, log=lambda _m: None):
        self._cache = {}  # url -> (fetched_at, value)
        self._lock = threading.Lock()
        self._clock = clock
        self._log = log

    def get_json(self, url, ttl=300, fallback=None):
        """Return parsed JSON for `url`, refetching only once older than `ttl`.

        On fetch failure: return the cached value if we have one, else `fallback`
        if provided, else re-raise.
        """
        now = self._clock()
        with self._lock:
            entry = self._cache.get(url)
        if entry and (now - entry[0]) < ttl:
            return entry[1]

        try:
            value = self._fetch(url)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully on any error
            self._log(f"data: fetch failed for {url}: {exc}")
            if entry is not None:
                return entry[1]
            if fallback is not None:
                return fallback
            raise

        with self._lock:
            self._cache[url] = (now, value)
        return value

    @staticmethod
    def _fetch(url):
        if url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        with open(url, "r", encoding="utf-8") as handle:
            return json.load(handle)
