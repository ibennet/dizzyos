#!/usr/bin/env python3
"""Kernel smoke test — no hardware, no network, runs in CI.

Covers the system layer end to end: overlay compose (no-wifi icon, PIN
banner + TTL expiry), the network monitor's offline/online transitions, and
the settings server's whole PIN auth + config-save flow over real HTTP on a
loopback port. Exits non-zero on the first failure.
"""

import os
import re
import sys
import tempfile
import time
import urllib.error
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
# The monitor publishes state via on_change; the overlay wiring lives in the
# caller (run.py), mirrored here. Debounced: several fails to go offline.
print("netmon")
overlays = OverlayManager()


def on_net(online):
    overlays.hide("no_wifi") if online else overlays.show("no_wifi", no_wifi_icon)


monitor = NetworkMonitor(log=lambda m: None, interval=0.01,
                         probe=lambda: False, on_change=on_net).start()
time.sleep(0.3)
check("consecutive failures go offline and show the icon",
      not monitor.online and "no_wifi" in overlays.active())
monitor._probe = lambda: True
time.sleep(0.3)
check("a success comes back online and hides it",
      monitor.online and "no_wifi" not in overlays.active())
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


def post(path, opener=None, **fields):
    data = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in fields.items())
    try:
        return (opener or http).open(base + path, data=data.encode(), timeout=10)
    except urllib.error.HTTPError as exc:
        return exc


def status_for_host(host):
    req = urllib.request.Request(base + "/", headers={"Host": host})
    try:
        return http.open(req, timeout=10).status
    except urllib.error.HTTPError as exc:
        return exc.code


landing = get("/").read().decode()
check("unauthenticated landing offers the PIN button", "show PIN" in landing)
check("a bare GET does NOT put a PIN on the sign", "setup_pin" not in overlays.active())

# The Host-header guard (DNS-rebinding defence) rejects a foreign Host.
check("a foreign Host header is rejected", status_for_host("evil.example") == 403)

# Issuing the PIN is a POST behind the button.
post("/pin")
check("requesting the PIN puts it on the sign", "setup_pin" in overlays.active())

pin = server._pin[0]
bad_pin = "000000" if pin != "000000" else "999999"
check("wrong PIN is rejected", post("/auth", pin=bad_pin).code == 403)
authed = post("/auth", pin=pin).read().decode()
check("right PIN grants a session", "config.yaml" in authed)

csrf = re.search(r'name="csrf" value="([^"]+)"', authed).group(1)

# A session from another device (no cookie) can't write.
nocookie = urllib.request.build_opener()  # no CookieProcessor
before = open(cfg_path, encoding="utf-8").read()
post("/config", opener=nocookie, csrf=csrf, config="matrix:\n  rows: 8\n  cols: 8\n")
check("POST without a session cookie does not write",
      open(cfg_path, encoding="utf-8").read() == before)

# A valid session but a missing/wrong CSRF token is refused.
check("POST without a CSRF token is refused",
      post("/config", config="matrix:\n  rows: 8\n  cols: 8\n").code == 403)

# post() follows the redirect back to the settings page, which carries the
# one-shot status message — read it off that response.
bad = post("/config", csrf=csrf, config=": not yaml : [")
check("invalid YAML is refused", "not saved" in bad.read().decode())
with open(cfg_path, encoding="utf-8") as fh:
    check("refused YAML did not touch the file", "rows: 64" in fh.read())

post("/config", csrf=csrf, config="matrix:\n  rows: 32\n  cols: 32\n")
with open(cfg_path, encoding="utf-8") as fh:
    check("valid YAML is written", "rows: 32" in fh.read())
check("save triggers a restart", restarts == [1])
with open(cfg_path + ".prev", encoding="utf-8") as fh:
    check("atomic write keeps the previous config as .prev", "rows: 64" in fh.read())

os.unlink(cfg_path)
if os.path.exists(cfg_path + ".prev"):
    os.unlink(cfg_path + ".prev")

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
