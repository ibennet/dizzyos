"""Network monitor — a reachability sensor. It publishes state; it draws nothing.

A background thread probes connectivity every `interval` seconds (a TCP dial to
well-known DNS servers — cheap, no payload, and unlike ping it needs no
privileges). It exposes the current state as `.online` and calls `on_change`
when it flips; wiring that to the no-wifi overlay is run.py's job, not the
sensor's, so the signal is also available to apps (dim stale data) and the
settings page (show link state).

Debounced: a single dropped probe (or one resolver having a bad day) must not
flap the icon, so it takes several consecutive failures to go offline and one
success to come back. Probe-first: the first probe runs immediately, so a sign
that boots offline says so within one probe rather than one interval.
"""

import socket
import threading

#: (host, port) probe targets — two independent anycast resolvers, so one
#: provider having a bad day doesn't flag the sign offline.
_PROBES = (("1.1.1.1", 53), ("8.8.8.8", 53))

#: Consecutive failed probes before we declare the sign offline (debounce).
FAIL_THRESHOLD = 3


class NetworkMonitor:
    def __init__(self, log, interval=10, probe=None, on_change=None):
        self.log = log
        self.interval = interval
        self.online = True  # optimistic until the debounce says otherwise
        self._probe = probe or self._dial
        self._on_change = on_change
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

    def _set(self, online):
        if online == self.online:
            return
        self.online = online
        self.log("netmon: back online" if online else "netmon: offline")
        if self._on_change:
            self._on_change(online)

    def _loop(self):
        fails = 0
        while True:
            if self._probe():
                fails = 0
                self._set(True)
            else:
                fails += 1
                if fails >= FAIL_THRESHOLD:
                    self._set(False)
            if self._stop.wait(self.interval):  # probe first, then wait
                return
