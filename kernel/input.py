"""Optional physical controls via the Matrix Bonnet's spare GPIO.

The Bonnet breaks out a few unused GPIO pins; wiring momentary buttons to them lets
you jump between apps instead of waiting for rotation. This is a stub: on a Mac (or a
Pi without gpiozero) it imports as a no-op so the rest of the system runs unchanged.

Wire it up later by having buttons call `Controls.on_next` / `on_prev` / `on_select`,
which the launcher can subscribe to.
"""


class Controls:
    def __init__(self, next_pin=None, prev_pin=None, select_pin=None):
        self.available = False
        self.on_next = None
        self.on_prev = None
        self.on_select = None
        try:
            from gpiozero import Button  # noqa: F401 - only present on a configured Pi
        except Exception:  # noqa: BLE001
            return
        self._pins = (next_pin, prev_pin, select_pin)
        self.available = all(p is not None for p in self._pins)
        # Button wiring intentionally deferred until hardware is in hand.
