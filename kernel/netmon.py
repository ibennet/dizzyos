"""Network monitor — owns the red no-WiFi overlay.

A background thread probes connectivity every `interval` seconds (a TCP dial
to well-known DNS servers — cheap, no payload, and unlike ping it needs no
privileges). While offline, the no-wifi icon overlay is shown; the moment a
probe succeeds it is hidden. Apps never see any of this — their data fetches
already fail soft — the icon is for the human wondering why the sign is stale.
"""

import socket
import threading

from .overlay import no_wifi_icon

#: (host, port) probe targets — two independent anycast resolvers, so one
#: provider having a bad day doesn't flag the sign offline.
_PROBES = (("1.1.1.1", 53), ("8.8.8.8", 53))
_OVERLAY = "no_wifi"


class NetworkMonitor:
    def __init__(self, overlays, log, interval=10, probe=None):
        self.overlays = overlays
        self.log = log
        self.interval = interval
        self.online = True  # optimistic until the first probe says otherwise
        self._probe = probe or self._dial
        self._stop = threading.Event()

    @staticmethod
    def _dial():
        for host, port in _PROBES:
            try:
                socket.create_connection((host, port), timeout=3).close()
                return True
            except OSError:
                continue
        return False

    def start(self):
        threading.Thread(target=self._loop, name="netmon", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            was = self.online
            self.online = self._probe()
            if was and not self.online:
                self.log("netmon: offline — showing no-wifi icon")
                self.overlays.show(_OVERLAY, no_wifi_icon)
            elif not was and self.online:
                self.log("netmon: back online")
                self.overlays.hide(_OVERLAY)
