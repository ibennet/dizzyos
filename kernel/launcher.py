"""The launcher — dizzyos's tiny scheduler / window manager.

Rotates through the enabled apps: run each for its dwell time, then transition to the
next. Rendering is double-buffered via CreateFrameCanvas + SwapOnVSync for smooth
animation. Data refreshes happen off the render loop on each app's declared interval.

Clock and sleep are injected so the loop is testable without real time.
"""

import threading
import time

from PIL import Image


class Launcher:
    def __init__(self, matrix, apps, cfg, services, clock=time.monotonic, sleep=time.sleep):
        if not apps:
            raise ValueError("launcher needs at least one app")
        self.matrix = matrix
        self.apps = apps
        self.services = services
        self.clock = clock
        self.sleep = sleep

        launcher_cfg = cfg.get("launcher", {})
        self.default_dwell = launcher_cfg.get("default_dwell", 20)
        self.target_fps = launcher_cfg.get("target_fps", 24)
        self.transition_ms = launcher_cfg.get("transition_ms", 600)
        self.transition = launcher_cfg.get("transition", "crossfade")

    def run(self):
        """Run forever, cycling through apps. Ctrl-C to stop."""
        canvas = self.matrix.CreateFrameCanvas()
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
        app.on_start(self.services)
        stop_refresh = self._start_refresh(app)
        try:
            start = self.clock()
            dwell = app.dwell or self.default_dwell
            frame_time = 1.0 / self.target_fps
            while (self.clock() - start) < dwell:
                app.draw(canvas, self.clock() - start)
                canvas = self.matrix.SwapOnVSync(canvas)
                self.sleep(frame_time)
        finally:
            stop_refresh.set()
            app.on_stop()
        return canvas

    def _start_refresh(self, app):
        """Refresh once now, then re-refresh in the background on the app's interval."""
        app.refresh()
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
        """Crossfade from the outgoing app's last frame to the incoming app's first."""
        if self.transition != "crossfade" or self.transition_ms <= 0:
            return canvas
        incoming.on_start(self.services)
        incoming.refresh()
        frame_time = 1.0 / self.target_fps
        steps = max(int((self.transition_ms / 1000.0) * self.target_fps), 1)
        last = outgoing.render(outgoing.dwell or self.default_dwell).convert("RGB")
        for i in range(1, steps + 1):
            alpha = i / steps
            nxt = incoming.render((i / steps) * (frame_time * steps)).convert("RGB")
            blended = Image.blend(last, nxt, alpha)
            canvas.SetImage(blended)
            canvas = self.matrix.SwapOnVSync(canvas)
            self.sleep(frame_time)
        incoming.on_stop()  # will be re-started by the next _run_app
        return canvas
