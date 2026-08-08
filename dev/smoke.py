#!/usr/bin/env python3
"""Kernel smoke test — no hardware, no network, runs in CI.

Covers the system layer end to end: overlay compose (no-wifi icon, PIN
banner + TTL expiry), the network monitor's offline/online transitions, and
the settings server's whole PIN auth + config-save flow over real HTTP on a
loopback port. Exits non-zero on the first failure.
"""

import os
import sys
import tempfile
import time
import urllib.request
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from kernel.netmon import NetworkMonitor
from kernel.overlay import OverlayManager, no_wifi_icon, pin_banner
from kernel.render import FontBook
from kernel.settings import SettingsServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = FontBook(os.path.join(ROOT, "fonts"))

passed = 0


def check(name, cond):
    global passed
    if not cond:
        sys.exit(f"FAIL: {name}")
    passed += 1
    print(f"  ok: {name}")


def has_color(frame, predicate):
    return any(predicate(px) for px in frame.getdata())


def frame():
    return Image.new("RGB", (128, 64), "black")


# --- overlays ---------------------------------------------------------------
print("overlays")
fake_now = [0.0]
overlays = OverlayManager(clock=lambda: fake_now[0])

overlays.show("no_wifi", no_wifi_icon)
composed = overlays.compose(frame())
check("no-wifi icon draws red pixels",
      has_color(composed, lambda px: px[0] > 200 and px[1] < 100))
overlays.hide("no_wifi")
check("hidden overlay stops drawing",
      not has_color(overlays.compose(frame()), lambda px: px != (0, 0, 0)))

overlays.show("setup_pin", pin_banner(FONTS, "4127"), ttl=30)
composed = overlays.compose(frame())
check("pin banner draws white digits",
      has_color(composed, lambda px: px == (255, 255, 255)))
fake_now[0] = 31.0
check("pin banner expires after its ttl",
      not has_color(overlays.compose(frame()), lambda px: px != (0, 0, 0)))

# --- network monitor --------------------------------------------------------
print("netmon")
overlays = OverlayManager()
monitor = NetworkMonitor(overlays, log=lambda m: None, interval=0.01,
                         probe=lambda: False).start()
time.sleep(0.3)
check("offline shows the no-wifi overlay",
      not monitor.online and "no_wifi" in overlays.active())
monitor._probe = lambda: True
time.sleep(0.3)
check("recovery hides it", monitor.online and "no_wifi" not in overlays.active())
monitor.stop()

# --- settings server --------------------------------------------------------
print("settings server")
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cfg:
    cfg.write("matrix:\n  rows: 64\n  cols: 64\n")
    cfg_path = cfg.name

restarts = []
overlays = OverlayManager()
server = SettingsServer(cfg_path, overlays, FONTS, log=lambda m: None,
                        version="smoke", port=0,
                        restart=lambda: (restarts.append(1), "saved")[1])
server.start()
base = f"http://127.0.0.1:{server.port}"
jar = CookieJar()
http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    return http.open(base + path, timeout=10)


def post(path, **fields):
    data = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in fields.items())
    try:
        return http.open(base + path, data=data.encode(), timeout=10)
    except urllib.error.HTTPError as exc:
        return exc


page = get("/").read().decode()
check("unauthenticated page asks for the PIN", "PIN" in page)
check("opening the page puts the PIN on the sign", "setup_pin" in overlays.active())

pin = server._pin[0]
check("wrong PIN is rejected", post("/auth", pin="0000" if pin != "0000" else "9999").code == 403)
check("right PIN grants a session", "config.yaml" in post("/auth", pin=pin).read().decode())

# post() follows the redirect back to the settings page, which carries the
# one-shot status message — read it off that response.
bad = post("/config", config=": not yaml : [")
check("invalid YAML is refused", "not saved" in bad.read().decode())
with open(cfg_path, encoding="utf-8") as fh:
    check("refused YAML did not touch the file", "rows: 64" in fh.read())

post("/config", config="matrix:\n  rows: 32\n  cols: 32\n")
with open(cfg_path, encoding="utf-8") as fh:
    check("valid YAML is written", "rows: 32" in fh.read())
check("save triggers a restart", restarts == [1])

os.unlink(cfg_path)

# --- launcher survives a broken app -----------------------------------------
# Regression: an app whose layout assumed a 64-row canvas raised every frame
# on a 32-row one and took the whole service down, so systemd restart-looped
# and NO app rendered. One bad app must cost one slot, not the sign.
print("launcher resilience")
from kernel.app import App
from kernel.launcher import Launcher


class FakeCanvas:
    def SetImage(self, img):
        self.last = img


class FakeMatrix:
    def CreateFrameCanvas(self):
        return FakeCanvas()

    def SwapOnVSync(self, canvas):
        return canvas


class BrokenApp(App):
    name = "broken"

    def render(self, t):
        raise ValueError("y1 must be greater than or equal to y0")


class GoodApp(App):
    name = "good"

    def __init__(self):
        super().__init__()
        self.frames = 0

    def render(self, t):
        self.frames += 1
        return Image.new("RGB", (128, 32), "blue")


logged = []
services = type("S", (), {"width": 128, "height": 32, "fonts": FONTS,
                          "log": logged.append})()
good = GoodApp()
ticks = [0.0]
launcher = Launcher(FakeMatrix(), [BrokenApp(), good], {"launcher": {"default_dwell": 1,
                    "target_fps": 4, "transition": "none"}}, services,
                    clock=lambda: ticks[0], sleep=lambda s: ticks.__setitem__(0, ticks[0] + s))

canvas = launcher._run_app(BrokenApp(), FakeCanvas())
check("broken app does not raise out of the launcher", canvas is not None)
check("broken app is logged", any("render failed" in m for m in logged))
ticks[0] = 0.0
launcher._run_app(good, FakeCanvas())
check("a good app still renders after a broken one", good.frames > 0)

print(f"\nsmoke: all {passed} checks passed")
