"""LAN settings server — configure the sign from any browser on the network.

No monitor, no keyboard, no app: browse to http://<sign>.local:8080. Access
is gated by physical presence — opening the page puts a one-time PIN on the
sign itself for 30 seconds (see kernel/overlay.py), and only someone who can
read the panels can log in. After that: edit config.yaml (validated before
it is written), join a different WiFi network, see version/status.

Plain stdlib http.server in a daemon thread; it must never take the render
loop down with it. On the Pi a config save schedules a service restart; in
dev (no systemd) it just tells you to restart by hand.
"""

import html
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import yaml

from .overlay import pin_banner

PIN_TTL = 30          # seconds the PIN shows on the sign
PIN_MAX_ATTEMPTS = 5  # wrong guesses before the PIN is void
SESSION_TTL = 30 * 60

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dizzyos</title><style>
 body {{ font-family: ui-monospace, monospace; background: #111; color: #ddd;
        max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
 h1 {{ color: #f4b942; font-size: 1.3rem; }}  h2 {{ font-size: 1rem; color: #aaa; }}
 input, textarea {{ background: #1c1c1e; color: #eee; border: 1px solid #444;
        padding: .5rem; font: inherit; width: 100%; box-sizing: border-box; }}
 textarea {{ height: 22rem; }}
 button {{ background: #f4b942; color: #111; border: 0; padding: .5rem 1.2rem;
        font: inherit; font-weight: bold; margin-top: .5rem; cursor: pointer; }}
 .err {{ color: #ff6b5e; }} .ok {{ color: #7bd88f; }}
 dt {{ color: #888; float: left; width: 8rem; }} dd {{ margin: 0 0 .3rem 8rem; }}
</style></head><body><h1>dizzyos</h1>{body}</body></html>"""


def _now():
    return time.monotonic()


class SettingsServer:
    def __init__(self, config_path, overlays, fonts, log, version="dev",
                 port=8080, restart=None):
        self.config_path = config_path
        self.overlays = overlays
        self.fonts = fonts
        self.log = log
        self.version = version
        self.port = port
        # How a config save takes effect. Default: systemd restart on the Pi,
        # a log line in dev. Injectable for tests.
        self.restart = restart if restart is not None else self._default_restart
        self._pin = None            # (pin, expires_at, attempts_left)
        self._sessions = {}         # token -> expires_at
        self._flash = {}            # token -> one-shot status message (msg, ok)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ auth
    def _issue_pin(self):
        """Mint a PIN and put it on the sign. Reuses the live one so a page
        reload doesn't invalidate the code someone is walking over to read."""
        with self._lock:
            if self._pin and self._pin[1] > _now() and self._pin[2] > 0:
                pin = self._pin[0]
            else:
                pin = f"{secrets.randbelow(10000):04d}"
                self._pin = (pin, _now() + PIN_TTL, PIN_MAX_ATTEMPTS)
        self.overlays.show("setup_pin", pin_banner(self.fonts, pin), ttl=PIN_TTL)
        self.log(f"settings: PIN displayed on sign for {PIN_TTL}s")

    def _check_pin(self, guess):
        time.sleep(1)  # flat-rate throttle on guessing
        with self._lock:
            if not self._pin or self._pin[1] <= _now() or self._pin[2] <= 0:
                return False
            pin, expires, attempts = self._pin
            if secrets.compare_digest(pin, guess.strip()):
                self._pin = None
                return True
            self._pin = (pin, expires, attempts - 1)
            return False

    def _new_session(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = _now() + SESSION_TTL
        return token

    def _session_ok(self, token):
        with self._lock:
            self._sessions = {t: exp for t, exp in self._sessions.items()
                              if exp > _now()}
            return token in self._sessions

    # ------------------------------------------------------------------ ops
    def _default_restart(self):
        if shutil.which("systemctl"):
            # Let the HTTP response flush before the service (and us) restarts.
            threading.Timer(1.5, lambda: subprocess.run(
                ["systemctl", "restart", "dizzyos"], check=False)).start()
            return "saved — sign restarting with the new config"
        return "saved — restart dizzyos to apply"

    def save_config(self, text):
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return f"not saved — invalid YAML: {exc}", False
        if not isinstance(parsed, dict) or "matrix" not in parsed:
            return "not saved — this doesn't look like a dizzyos config", False
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.log("settings: config.yaml updated via LAN settings page")
        return self.restart(), True

    def join_wifi(self, ssid, password):
        if not shutil.which("nmcli"):
            return "no nmcli here (dev machine?) — WiFi join is Pi-only", False
        result = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return f"joined '{ssid}'", True
        return f"failed to join '{ssid}': {result.stderr.strip()}", False

    # ------------------------------------------------------------------ http
    def start(self):
        server = ThreadingHTTPServer(("", self.port), self._handler_class())
        server.daemon_threads = True
        self.port = server.server_address[1]  # resolves port 0 (tests) to real
        threading.Thread(target=server.serve_forever, name="settings",
                         daemon=True).start()
        self.log(f"settings: LAN settings page on port {self.port}")
        return self

    def _handler_class(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # route through the kernel log
                outer.log("settings: " + fmt % args)

            # -- helpers --
            def _token(self):
                cookies = self.headers.get("Cookie", "")
                for part in cookies.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == "dizzyos_session":
                        return value
                return None

            def _send(self, body, status=200, headers=()):
                data = _PAGE.format(body=body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def _redirect(self, location, headers=()):
                self.send_response(303)
                self.send_header("Location", location)
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()

            def _form(self):
                length = int(self.headers.get("Content-Length", 0))
                return parse_qs(self.rfile.read(length).decode())

            # -- pages --
            def do_GET(self):
                if self.path not in ("/", ""):
                    self._send("<p class=err>not found</p>", 404)
                    return
                if self._session_valid():
                    self._settings_page()
                else:
                    outer._issue_pin()
                    self._send(
                        "<p>a <b>4-digit PIN</b> is showing on the sign "
                        f"for {PIN_TTL} seconds.</p>"
                        '<form method="post" action="/auth">'
                        '<input name="pin" inputmode="numeric" autofocus '
                        'placeholder="PIN from the sign">'
                        "<button>unlock</button></form>")

            def _session_valid(self):
                token = self._token()
                return token and outer._session_ok(token)

            def _settings_page(self):
                token = self._token()
                msg, ok = outer._flash.pop(token, ("", True))
                status = (f'<p class="{"ok" if ok else "err"}">'
                          f"{html.escape(msg)}</p>" if msg else "")
                with open(outer.config_path, encoding="utf-8") as fh:
                    config_text = fh.read()
                self._send(
                    status
                    + "<dl>"
                    + f"<dt>version</dt><dd>{html.escape(outer.version)}</dd>"
                    + f"<dt>config</dt><dd>{html.escape(outer.config_path)}</dd>"
                    + "</dl>"
                    + "<h2>config.yaml</h2>"
                    '<form method="post" action="/config">'
                    f"<textarea name=\"config\">{html.escape(config_text)}</textarea>"
                    "<button>save + restart</button></form>"
                    "<h2>join a different WiFi network</h2>"
                    '<form method="post" action="/wifi">'
                    '<input name="ssid" placeholder="network name"><br>'
                    '<input name="password" type="password" placeholder="password">'
                    "<button>join</button></form>")

            def do_POST(self):
                form = self._form()
                if self.path == "/auth":
                    guess = form.get("pin", [""])[0]
                    if outer._check_pin(guess):
                        token = outer._new_session()
                        self._redirect("/", [(
                            "Set-Cookie",
                            f"dizzyos_session={token}; HttpOnly; SameSite=Lax; "
                            f"Max-Age={SESSION_TTL}")])
                    else:
                        self._send('<p class=err>wrong or expired PIN — '
                                   '<a href="/">try again</a></p>', 403)
                    return
                if not self._session_valid():
                    self._redirect("/")
                    return
                token = self._token()
                if self.path == "/config":
                    outer._flash[token] = outer.save_config(
                        form.get("config", [""])[0])
                elif self.path == "/wifi":
                    outer._flash[token] = outer.join_wifi(
                        form.get("ssid", [""])[0], form.get("password", [""])[0])
                self._redirect("/")

        return Handler
