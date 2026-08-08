"""The launcher — dizzyos's tiny scheduler / window manager.

Rotates through the enabled apps: run each for its dwell time, then transition to the
next. Rendering is double-buffered via CreateFrameCanvas + SwapOnVSync for smooth
animation. Data refreshes happen off the render loop on each app's declared interval.

Clock and sleep are injected so the loop is testable without real time.
"""

import os
import threading
import time

from PIL import Image, ImageDraw

#: Transition styles selectable via `launcher.transition` in config.yaml.
TRANSITIONS = ("crossfade", "slide", "wipe", "blank_wipe", "cut_wipe", "none")

#: Fraction of a `cut_wipe` spent holding on black before the incoming frame wipes in.
CUT_HOLD = 0.15


def _ease_in_out(t):
    """Cubic ease-in-out — slow at both ends, quick through the middle. What makes a
    slide feel deliberate rather than mechanical."""
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def compose_transition(style, last, nxt, alpha):
    """Blend the outgoing frame `last` into the incoming frame `nxt` at progress
    `alpha` (0..1), in the given style. Returns a new RGB image."""
    width, height = last.size

    if style == "slide":
        # Both frames travel together, iPad-style: outgoing exits left as the
        # incoming follows it in from the right.
        offset = int(_ease_in_out(alpha) * width)
        frame = Image.new("RGB", (width, height), "black")
        frame.paste(last, (-offset, 0))
        frame.paste(nxt, (width - offset, 0))
        return frame

    if style == "wipe":
        # Neither frame moves; the incoming one is revealed left to right.
        frame = last.copy()
        cut = int(alpha * width)
        if cut > 0:
            frame.paste(nxt.crop((0, 0, cut, height)), (0, 0))
        return frame

    if style == "blank_wipe":
        # Two beats: wipe the outgoing frame away to black, then wipe the incoming
        # one in over that black. Reads as a deliberate "clear the sign" gesture.
        frame = Image.new("RGB", (width, height), "black")
        if alpha < 0.5:
            cut = int(alpha * 2 * width)
            frame.paste(last.crop((cut, 0, width, height)), (cut, 0))
        else:
            cut = int((alpha - 0.5) * 2 * width)
            if cut > 0:
                frame.paste(nxt.crop((0, 0, cut, height)), (0, 0))
        return frame

    if style == "cut_wipe":
        # The outgoing frame is cut to black in a single frame — no wipe-away — then
        # after a short hold the incoming one wipes in. The blackout is the beat.
        frame = Image.new("RGB", (width, height), "black")
        if alpha > CUT_HOLD:
            cut = int((alpha - CUT_HOLD) / (1 - CUT_HOLD) * width)
            if cut > 0:
                frame.paste(nxt.crop((0, 0, cut, height)), (0, 0))
        return frame

    return Image.blend(last, nxt, alpha)  # crossfade, and the fallback


class Launcher:
    def __init__(self, matrix, apps, cfg, services, overlays=None,
                 clock=time.monotonic, sleep=time.sleep):
        # An empty app list is not fatal: the run loop holds on a kernel
        # fallback frame (keeping overlays and the settings page alive) rather
        # than crashing the process into a restart loop.
        self.matrix = matrix
        self.apps = apps
        self.services = services
        self.overlays = overlays
        self.clock = clock
        self.sleep = sleep
        # Touched once per displayed frame when set (systemd points it at
        # /run/dizzyos/heartbeat); dizzyos-update reads it as proof the new
        # release actually renders — its post-deploy health check.
        self.heartbeat_path = os.environ.get("DIZZYOS_HEARTBEAT")

        launcher_cfg = cfg.get("launcher", {})
        self.default_dwell = launcher_cfg.get("default_dwell", 20)
        self.target_fps = launcher_cfg.get("target_fps", 24)
        self.transition_ms = launcher_cfg.get("transition_ms", 600)
        self.transition = launcher_cfg.get("transition", "crossfade")

    def run(self):
        """Run forever, cycling through apps. Ctrl-C to stop."""
        canvas = self.matrix.CreateFrameCanvas()
        if not self.apps:
            # Nothing loaded (e.g. every rotation entry was bad). Hold on the
            # fallback frame so overlays stay visible and the sign is fixable
            # via the settings page, instead of exiting into a restart loop.
            while True:
                canvas = self._render_fallback(canvas, ["no apps", "fix at :8080"])
        index = 0
        while True:
            app = self.apps[index]
            canvas = self._run_app(app, canvas)
            if len(self.apps) > 1:
                nxt = self.apps[(index + 1) % len(self.apps)]
                canvas = self._transition_to(nxt, app, canvas)
            index = (index + 1) % len(self.apps)

    # ------------------------------------------------------------------
    def _run_app(self, app, canvas):
        self._safe(app, "on_start", self.services)
        stop_refresh = self._start_refresh(app)
        try:
            start = self.clock()
            dwell = app.dwell or self.default_dwell
            frame_time = 1.0 / self.target_fps
            while (self.clock() - start) < dwell:
                frame = self._render(app, self.clock() - start)
                if frame is None:  # app is broken — show the fallback, don't
                    # hot-spin: _render_fallback paces itself, so a rotation of
                    # all-broken apps stays at the frame rate rather than pegging
                    # the CPU and churning refresh threads.
                    canvas = self._render_fallback(canvas, [app.name, "not rendering"])
                    continue
                canvas.SetImage(self._compose(frame))
                canvas = self.matrix.SwapOnVSync(canvas)
                self._beat()
                self.sleep(frame_time)
        finally:
            stop_refresh.set()
            self._safe(app, "on_stop")
        return canvas

    def _safe(self, app, method, *args):
        """Call an app lifecycle hook (on_start/refresh/on_stop), swallowing and
        logging any exception. One app's bug must cost only its slot in the
        rotation, not the whole sign — the rule _render applies to render()."""
        try:
            return getattr(app, method)(*args)
        except Exception as exc:  # noqa: BLE001 - any app's bug, not just ours
            self.services.log(f"{app.name}: {method} failed: {exc}")
            return None

    def _render(self, app, t):
        """One frame from `app`, or None if it raised.

        A broken app must not take the whole sign down. Found on hardware: an
        app whose layout assumed a 64-row canvas raised on every frame of a
        32-row one, killing the process — and `Restart=always` turned that
        into an endless restart loop where the other two apps never rendered
        either. Skipping the app costs one slot in the rotation; crashing
        costs the whole sign.
        """
        try:
            return app.render(t)
        except Exception as exc:  # noqa: BLE001 - any app's bug, not just ours
            self.services.log(f"{app.name}: render failed, skipping its turn: {exc}")
            return None

    def _start_refresh(self, app):
        """Refresh once now, then re-refresh in the background on the app's interval."""
        self._safe(app, "refresh")
        stop = threading.Event()
        interval = app.refresh_interval
        if interval:
            def loop():
                while not stop.wait(interval):
                    try:
                        app.refresh()
                    except Exception as exc:  # noqa: BLE001
                        self.services.log(f"{app.name}: refresh error: {exc}")
            threading.Thread(target=loop, daemon=True).start()
        return stop

    def _transition_to(self, incoming, outgoing, canvas):
        """Animate from the outgoing app's last frame to the incoming app's first,
        in the configured style (see TRANSITIONS / compose_transition)."""
        if self.transition == "none" or self.transition_ms <= 0:
            return canvas
        if self.transition not in TRANSITIONS:
            self.services.log(
                f"launcher: unknown transition {self.transition!r}, using crossfade"
            )
        # Same guarantee as _start_refresh: a bad app at the transition boundary
        # (raising in on_start or refresh) must not take the whole sign down.
        self._safe(incoming, "on_start", self.services)
        self._safe(incoming, "refresh")
        frame_time = 1.0 / self.target_fps
        duration = self.transition_ms / 1000.0
        steps = max(int(duration * self.target_fps), 1)
        last = self._render(outgoing, outgoing.dwell or self.default_dwell)
        if last is None:  # nothing to transition from — cut straight over
            self._safe(incoming, "on_stop")
            return canvas
        last = last.convert("RGB")
        for i in range(1, steps + 1):
            alpha = i / steps
            nxt = self._render(incoming, alpha * duration)
            if nxt is None:  # broken incoming app; _run_app will skip it too
                break
            nxt = nxt.convert("RGB")
            frame = compose_transition(self.transition, last, nxt, alpha)
            canvas.SetImage(self._compose(frame))
            canvas = self.matrix.SwapOnVSync(canvas)
            self._beat()
            self.sleep(frame_time)
        self._safe(incoming, "on_stop")  # will be re-started by the next _run_app
        return canvas

    # ------------------------------------------------------------------
    def _compose(self, frame):
        """A finished frame -> what actually hits the panels: RGB, with any
        system overlays (no-wifi icon, setup PIN) composited on top."""
        # convert() to the same mode returns a *copy*, which is load-bearing:
        # overlays composite in place, and an app may hand us a cached/pre-
        # rendered Image — without this copy the overlay would burn into it.
        frame = frame.convert("RGB")
        if self.overlays:
            frame = self.overlays.compose(frame)
        return frame

    def _render_fallback(self, canvas, lines):
        """Draw a kernel-owned frame (a short centered message) and push it,
        self-paced at the frame rate. Used when no app is rendering — an empty
        rotation, or every app broken — so the panel keeps refreshing and any
        no-wifi / setup-PIN overlay stays visible instead of the sign going dark
        or the loop hot-spinning."""
        from .pixelfont import GLYPH_H
        frame = Image.new("RGB", (self.services.width, self.services.height), "black")
        font = self.services.fonts.pixel()
        draw = ImageDraw.Draw(frame)
        total_h = len(lines) * GLYPH_H + (len(lines) - 1) * 2
        y = max((frame.height - total_h) // 2, 0)
        for line in lines:
            w = font.measure(line)
            font.draw_text(draw, max((frame.width - w) // 2, 0), y, line, (120, 120, 130))
            y += GLYPH_H + 2
        canvas.SetImage(self._compose(frame))
        canvas = self.matrix.SwapOnVSync(canvas)
        self._beat()
        self.sleep(1.0 / self.target_fps)
        return canvas

    def _beat(self):
        """Prove liveness to dizzyos-update (see heartbeat_path above)."""
        if self.heartbeat_path:
            try:
                with open(self.heartbeat_path, "w"):
                    pass
            except OSError:
                self.heartbeat_path = None  # e.g. /run dir missing in dev
