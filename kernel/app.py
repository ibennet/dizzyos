"""The App contract — the entire extensibility surface of dizzyos.

An app is a self-contained module under `apps/<name>/`. It renders frames and,
optionally, pulls data. The launcher handles when it runs, for how long, and the
transitions between apps. Apps never touch the matrix directly — they return a
PIL image sized to the canvas and the kernel pushes it to the panels.
"""

from PIL import Image


class App:
    #: Human-readable name; overridden from the app's manifest.
    name = "app"

    def __init__(self, config=None):
        self.config = config or {}
        self.services = None

    # --- lifecycle ---------------------------------------------------------
    def on_start(self, services):
        """Called when the app gains focus. Stash services, set up state."""
        self.services = services

    def refresh(self):
        """Pull fresh data. Called off the render loop (never per-frame)."""

    def on_stop(self):
        """Called when the app loses focus. Tear down state."""

    # --- rendering ---------------------------------------------------------
    def render(self, t):
        """Return a PIL.Image for elapsed time `t` (seconds). Animate here."""
        raise NotImplementedError

    def draw(self, canvas, t):
        """Default frame draw: render an image and blit it to the matrix canvas."""
        canvas.SetImage(self.render(t).convert("RGB"))

    # --- helpers -----------------------------------------------------------
    def blank(self):
        """A black, canvas-sized RGB image to draw onto."""
        return Image.new("RGB", (self.services.width, self.services.height), "black")

    @property
    def dwell(self):
        """Seconds to stay on screen, or None to use the launcher default."""
        return self.config.get("dwell")

    @property
    def refresh_interval(self):
        """Seconds between data refreshes, or None to never auto-refresh."""
        return self.config.get("refresh_interval")
