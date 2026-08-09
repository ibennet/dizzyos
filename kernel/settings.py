"""LAN settings server — configure the sign from any browser on the network.

No monitor, no keyboard, no app: browse to http://<sign>.local:8080 and press
"show PIN" — a one-time PIN appears on the sign for 30 seconds (see
kernel/overlay.py); type what you can read off the panels to log in. After
that: edit config.yaml (validated before it is written), join a different WiFi
network, see version/status, check for a newer release and trigger the update.

Threat model (see docs/adr/0002 §b): this gates against off-LAN and
not-in-the-room access. It is NOT a boundary against a trusted peer on the same
WiFi — they can see the sign, and on a shared-PSK network could sniff the PIN
off cleartext HTTP. What the server *does* defend, even LAN-only: brute force
(6-digit PIN, global 1/s throttle, lockout), cross-origin abuse (Host-header
check kills DNS rebinding; a CSRF token guards the write forms), and running
the privileged ops (config write, restart, nmcli) through narrow sudo helpers
so the render process itself never keeps root.

Plain stdlib http.server in a daemon thread; it must never take the render
loop down with it. On the Pi a config save schedules a service restart; in
dev (no systemd) it just tells you to restart by hand.
"""

import html
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import yaml

from .overlay import pin_banner

PIN_TTL = 30          # seconds the PIN shows on the sign
PIN_DIGITS = 6        # 10^6 space; a 4-digit PIN is brute-forceable in minutes
PIN_MAX_ATTEMPTS = 5  # wrong guesses before this PIN is void (a new one re-mints)
# After this many failed guesses across re-mints, refuse to issue for a cooldown
# — this is what actually bounds brute force, since a void PIN otherwise just
# re-mints with a fresh attempt budget.
AUTH_FAILS_BEFORE_COOLDOWN = 10
AUTH_COOLDOWN = 300   # seconds locked out after too many wrong guesses
SESSION_TTL = 10 * 60  # short: the cookie rides cleartext HTTP on a shared LAN
MAX_BODY = 256 * 1024  # refuse oversized POST bodies (Pi Zero 2 W has 512MB)

# Pi-only privilege helpers installed by bootstrap.sh; the render process runs
# as `daemon` after the matrix library drops privileges, so these narrow,
# fixed-purpose helpers (allowed via /etc/sudoers.d) do the root-needing bits.
WRITE_HELPER = "/opt/dizzyos/write-config"
NMCLI_HELPER = "/opt/dizzyos/nmcli-join"

# Same file tools/pi/dizzyos-update reads; tells us which repo to check for a
# newer release. Absent in dev, where the default below applies.
UPDATE_CONF = "/etc/dizzyos/update.conf"
DEFAULT_REPO = "ibennet/dizzyos"

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


def _host_allowed(host_header, hostname):
    """True if the request's Host header names *this* device — not an attacker
    domain that briefly resolved here. This is the DNS-rebinding guard: a page
    on evil.com whose A record flips to the sign's LAN IP still sends
    Host: evil.com, so it is rejected and never becomes same-origin. Bare IP
    literals are allowed (rebinding needs a *name* that re-resolves)."""
    if not host_header:
        return False
    host = host_header
    if host.startswith("["):        # [ipv6]:port
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0]  # strip :port (IPv4/name)
    host = host.lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    hn = hostname.lower()
    return host in (hn, hn + ".local")


def _norm_version(tag):
    return tag[1:] if tag.startswith("v") else tag


def _latest_release_tag():
    """Newest published release tag for the repo this sign updates from, or
    None before the first release has been cut. Mirrors conf()/latest_tag() in
    tools/pi/dizzyos-update — a few lines of stdlib duplicated rather than
    importing a root-owned script from /opt."""
    repo = DEFAULT_REPO
    if os.path.exists(UPDATE_CONF):
        with open(UPDATE_CONF, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.lstrip().startswith("#"):
                    key, _, val = line.partition("=")
                    if key.strip() == "REPO" and val.strip():
                        repo = val.strip()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "dizzyos-settings"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)["tag_name"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _basic_validate(cfg):
    """Minimal 'is this a dizzyos config' check — the default when the caller
    injects nothing (dev, tests). run.py passes a stricter validator that
    exercises the real boot path so the page can't save a config that would
    send the sign into a restart loop. Returns None if OK, else a reason."""
    if not isinstance(cfg, dict) or "matrix" not in cfg:
        return "this doesn't look like a dizzyos config"
    m = cfg["matrix"]
    if not isinstance(m, dict):
        return "matrix: must be a mapping"
    for key in ("rows", "cols"):
        if not isinstance(m.get(key), int) or m[key] <= 0:
            return f"matrix.{key} must be a positive integer"
    for key in ("chain", "parallel"):
        if key in m and not isinstance(m[key], int):
            return f"matrix.{key} must be an integer"
    return None


class SettingsServer:
    def __init__(self, config_path, overlays, fonts, log, version="dev",
                 port=8080, restart=None, validate=None, fetch_latest=None,
                 start_update=None):
        self.config_path = config_path
        self.overlays = overlays
        self.fonts = fonts
        self.log = log
        self.version = version
        self.port = port
        # How a config save takes effect. Default: systemd restart on the Pi,
        # a log line in dev. Injectable for tests.
        self.restart = restart if restart is not None else self._default_restart
        # Whether a candidate config is safe to write. Default is a shape check;
        # run.py injects one that walks the real load path (see validate_config).
        self.validate = validate if validate is not None else _basic_validate
        # Update flow, both halves injectable for tests: how we learn the
        # newest release tag, and how we kick off the install.
        self.fetch_latest = (fetch_latest if fetch_latest is not None
                             else _latest_release_tag)
        self.start_update = (start_update if start_update is not None
                             else self._default_start_update)
        self._update_available = None  # tag from the last check, if newer
        self.hostname = socket.gethostname()
        self._pin = None            # (pin, expires_at, attempts_left)
        self._fails = 0             # failed guesses since the last success/cooldown
        self._locked_until = 0.0    # brute-force cooldown deadline (monotonic)
        self._sessions = {}         # token -> (expires_at, client_ip, csrf)
        self._flash = {}            # token -> one-shot status message (msg, ok)
        self._lock = threading.Lock()
        self._auth_lock = threading.Lock()  # serialises /auth so the throttle is global

    # ------------------------------------------------------------------ auth
    def locked_out(self):
        with self._lock:
            return self._locked_until > _now()

    def _issue_pin(self):
        """Ensure a live PIN and show it on the sign. Returns 'cooldown' if we're
        locked out after too many wrong guesses, else 'shown'. A live PIN's
        banner is already up (same TTL), so we don't re-flash it — that both
        keeps a reload from invalidating the code someone is walking over to
        read, and bounds the display nuisance from repeated requests."""
        with self._lock:
            if self._locked_until > _now():
                return "cooldown"
            if self._pin and self._pin[1] > _now() and self._pin[2] > 0:
                return "shown"  # live PIN, banner still on the sign
            pin = f"{secrets.randbelow(10 ** PIN_DIGITS):0{PIN_DIGITS}d}"
            self._pin = (pin, _now() + PIN_TTL, PIN_MAX_ATTEMPTS)
        self.overlays.show("setup_pin", pin_banner(self.fonts, pin), ttl=PIN_TTL)
        self.log("settings: PIN displayed on sign")
        return "shown"

    def _register_fail(self):
        """Count a failed guess; trip the cooldown at the threshold. Caller holds
        self._lock."""
        self._fails += 1
        self.log(f"settings: failed PIN attempt ({self._fails})")
        if self._fails >= AUTH_FAILS_BEFORE_COOLDOWN:
            self._locked_until = _now() + AUTH_COOLDOWN
            self._fails = 0
            self.log(f"settings: too many failed attempts — locked {AUTH_COOLDOWN}s")

    def _check_pin(self, guess):
        guess = guess.strip()
        # _auth_lock serialises guesses so the 1s throttle is global, not
        # per-thread — otherwise concurrent POSTs on ThreadingHTTPServer would
        # each sleep independently and defeat it.
        with self._auth_lock:
            time.sleep(1)
            with self._lock:
                if self._locked_until > _now():
                    return False
                if not (guess.isdigit() and len(guess) == PIN_DIGITS):
                    self._register_fail()  # also avoids compare_digest TypeErrors
                    return False
                if not self._pin or self._pin[1] <= _now() or self._pin[2] <= 0:
                    return False
                pin, expires, attempts = self._pin
                if secrets.compare_digest(pin, guess):
                    self._pin = None
                    self._fails = 0
                    return True
                self._pin = (pin, expires, attempts - 1)
                self._register_fail()
                return False

    def _new_session(self, client_ip):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(16)
        with self._lock:
            self._sessions[token] = (_now() + SESSION_TTL, client_ip, csrf)
        return token

    def _session_ok(self, token, client_ip):
        with self._lock:
            self._sessions = {t: v for t, v in self._sessions.items()
                              if v[0] > _now()}
            entry = self._sessions.get(token)
            # Bind the session to the IP it was issued to: a cookie sniffed off
            # cleartext HTTP can't be replayed from another device on the LAN.
            return bool(entry) and entry[1] == client_ip

    def _csrf_for(self, token):
        with self._lock:
            entry = self._sessions.get(token)
            return entry[2] if entry else ""

    def _set_flash(self, token, value):
        with self._lock:
            self._flash[token] = value

    def _pop_flash(self, token):
        with self._lock:
            # Also drop flashes for sessions that no longer exist, so the dict
            # can't accumulate for tokens that never come back to read them.
            self._flash = {t: v for t, v in self._flash.items()
                           if t in self._sessions}
            return self._flash.pop(token, ("", True))

    # ------------------------------------------------------------------ ops
    def _default_restart(self):
        # The render process runs as `daemon`, so go through sudo (a narrow
        # sudoers rule allows exactly `systemctl restart dizzyos`). Fall back to
        # a bare systemctl if sudo isn't here, and to a hint in dev (no systemd).
        if shutil.which("systemctl"):
            cmd = (["sudo", "-n", "systemctl", "restart", "dizzyos"]
                   if shutil.which("sudo")
                   else ["systemctl", "restart", "dizzyos"])
            # Let the HTTP response flush before the service (and us) restarts.
            threading.Timer(1.5, lambda: subprocess.run(cmd, check=False)).start()
            return "saved — sign restarting with the new config"
        return "saved — restart dizzyos to apply"

    def save_config(self, text):
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return f"not saved — invalid YAML: {exc}", False
        reason = self.validate(parsed)
        if reason:
            # Rejected before the file is touched: a saved config that can't
            # boot would restart-loop with the recovery page unreachable.
            return f"not saved — {reason}", False
        try:
            self._atomic_write(text)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"not saved — could not write config ({exc})", False
        self.log("settings: config.yaml updated via LAN settings page")
        return self.restart(), True

    def _atomic_write(self, text):
        """Write config.yaml atomically (keep the old copy as `.prev`, write a
        sibling tempfile, os.replace). On the Pi the config dir is root-owned, so
        route through the fixed-target write-config sudo helper; the render
        process (daemon) never gets write access to /etc/dizzyos itself. In dev
        we own the file, so write it directly."""
        if shutil.which("sudo") and os.path.exists(WRITE_HELPER):
            subprocess.run(["sudo", "-n", WRITE_HELPER], input=text.encode(),
                           check=True, timeout=30)
            return
        path = self.config_path
        directory = os.path.dirname(os.path.abspath(path)) or "."
        if os.path.exists(path):
            shutil.copy2(path, path + ".prev")
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def update_available(self):
        with self._lock:
            return self._update_available

    def check_update(self):
        try:
            latest = self.fetch_latest()
        except Exception as exc:  # noqa: BLE001 — any network/API failure is
            # a status line on the page, never a 500 from the handler thread
            return f"update check failed: {exc}", False
        if latest is None:
            return "no releases published yet", True
        if _norm_version(latest) == _norm_version(self.version):
            with self._lock:
                self._update_available = None
            return f"up to date ({latest})", True
        with self._lock:
            self._update_available = latest
        return f"update available: {latest} (running {self.version})", True

    def run_update(self):
        with self._lock:
            latest = self._update_available
        if not latest:
            return "check for updates first", False
        ok, error = self.start_update()
        if not ok:
            return f"could not start update: {error}", False
        with self._lock:
            self._update_available = None
        self.log(f"settings: update to {latest} triggered via LAN settings page")
        return (f"updating to {latest} — the sign restarts when the install "
                "finishes; a release that fails its health check rolls back "
                "on its own"), True

    def _default_start_update(self):
        """Kick the existing updater unit (download, symlink flip, health
        check, rollback — see tools/pi/dizzyos-update) rather than
        reimplementing any of it here. Returns (ok, error). --no-block because
        the oneshot runs for minutes and ends by restarting this process."""
        if not shutil.which("systemctl"):
            return False, "no systemd here (dev machine?) — updating is Pi-only"
        cmd = ["systemctl", "start", "--no-block", "dizzyos-update"]
        if shutil.which("sudo"):
            cmd = ["sudo", "-n"] + cmd
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, result.stderr.strip() or f"exit {result.returncode}"
        return True, ""

    def join_wifi(self, ssid, password):
        # SSID/password are argv, never a shell string, so there's no injection
        # surface. On the Pi go through the nmcli-join sudo helper (daemon can't
        # run nmcli directly); in dev call nmcli straight if it happens to exist.
        if shutil.which("sudo") and os.path.exists(NMCLI_HELPER):
            cmd = ["sudo", "-n", NMCLI_HELPER, ssid, password]
        elif shutil.which("nmcli"):
            cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
        else:
            return "no nmcli here (dev machine?) — WiFi join is Pi-only", False
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"failed to join '{ssid}': {exc}", False
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
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    return {}
                if length < 0 or length > MAX_BODY:
                    return {}
                return parse_qs(self.rfile.read(length).decode("utf-8", "replace"))

            def _client_ip(self):
                return self.client_address[0]

            def _guard(self):
                """Common gate for every request: reject a Host header that isn't
                this device (DNS-rebinding defence). Returns True if handled (the
                caller should stop)."""
                if not _host_allowed(self.headers.get("Host"), outer.hostname):
                    self._send("<p class=err>bad host</p>", 403)
                    return True
                return False

            # -- pages --
            def do_GET(self):
                if self._guard():
                    return
                if self.path not in ("/", ""):
                    self._send("<p class=err>not found</p>", 404)
                    return
                if self._session_valid():
                    self._settings_page()
                else:
                    # Side-effect-free: showing the PIN (which lights the sign)
                    # is a POST behind a button, so a cross-site <img>/fetch or a
                    # LAN scanner can't park a banner on the display by GET alone.
                    self._send(
                        "<p>press the button — a one-time PIN will show on the "
                        "sign for 30 seconds.</p>"
                        '<form method="post" action="/pin">'
                        "<button>show PIN on the sign</button></form>")

            def _session_valid(self):
                token = self._token()
                return bool(token) and outer._session_ok(token, self._client_ip())

            def _pin_page(self):
                if outer._issue_pin() == "cooldown":
                    self._send("<p class=err>too many attempts — wait a few "
                               'minutes, then <a href="/">try again</a>.</p>', 429)
                    return
                self._send(
                    f"<p>a <b>{PIN_DIGITS}-digit PIN</b> is showing on the sign "
                    f"for {PIN_TTL} seconds.</p>"
                    '<form method="post" action="/auth">'
                    '<input name="pin" inputmode="numeric" autofocus '
                    'placeholder="PIN from the sign">'
                    "<button>unlock</button></form>")

            def _settings_page(self):
                token = self._token()
                csrf = html.escape(outer._csrf_for(token))
                msg, ok = outer._pop_flash(token)
                status = (f'<p class="{"ok" if ok else "err"}">'
                          f"{html.escape(msg)}</p>" if msg else "")
                with open(outer.config_path, encoding="utf-8") as fh:
                    config_text = fh.read()
                csrf_field = f'<input type="hidden" name="csrf" value="{csrf}">'
                update_section = (
                    "<h2>software update</h2>"
                    '<form method="post" action="/update/check">'
                    + csrf_field
                    + "<button>check for updates</button></form>")
                pending = outer.update_available()
                if pending:
                    update_section += (
                        '<form method="post" action="/update/run">'
                        + csrf_field
                        + f"<button>update now to {html.escape(pending)}"
                        "</button></form>")
                self._send(
                    status
                    + "<dl>"
                    + f"<dt>version</dt><dd>{html.escape(outer.version)}</dd>"
                    + f"<dt>config</dt><dd>{html.escape(outer.config_path)}</dd>"
                    + "</dl>"
                    + update_section
                    + "<h2>config.yaml</h2>"
                    '<form method="post" action="/config">'
                    + csrf_field
                    + f"<textarea name=\"config\">{html.escape(config_text)}</textarea>"
                    "<button>save + restart</button></form>"
                    "<h2>join a different WiFi network</h2>"
                    '<form method="post" action="/wifi">'
                    + csrf_field
                    + '<input name="ssid" placeholder="network name"><br>'
                    '<input name="password" type="password" placeholder="password">'
                    "<button>join</button></form>")

            def do_POST(self):
                if self._guard():
                    return
                form = self._form()
                if self.path == "/pin":
                    self._pin_page()
                    return
                if self.path == "/auth":
                    guess = form.get("pin", [""])[0]
                    if outer._check_pin(guess):
                        token = outer._new_session(self._client_ip())
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
                # CSRF: even with SameSite=Lax, don't let the write endpoints
                # rest on a cookie attribute alone. The token is per-session.
                if not secrets.compare_digest(form.get("csrf", [""])[0],
                                              outer._csrf_for(token)):
                    self._send("<p class=err>stale form — reload and retry</p>", 403)
                    return
                if self.path == "/config":
                    outer._set_flash(token, outer.save_config(
                        form.get("config", [""])[0]))
                elif self.path == "/wifi":
                    outer._set_flash(token, outer.join_wifi(
                        form.get("ssid", [""])[0], form.get("password", [""])[0]))
                elif self.path == "/update/check":
                    outer._set_flash(token, outer.check_update())
                elif self.path == "/update/run":
                    outer._set_flash(token, outer.run_update())
                self._redirect("/")

        return Handler
