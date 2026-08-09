# dizzyos

A tiny "operating system" for an LED-matrix sign. A small **kernel** provides shared
services (display, data, fonts, input) and self-contained **apps** plug in — each with
its own lifecycle. A **launcher** rotates through them with smooth transitions.

Runs on two chained Adafruit 64×64 HUB75 panels (a 128×64 canvas) driven by a
Raspberry Pi + Adafruit RGB Matrix HAT — and, thanks to a drop-in emulator, on your
Mac with **zero code changes**.

Apps so far: **Cafe Menu**, which renders [Izzy's Cafe](https://izzybennett.com/izzys-cafe/)
from the site's `/izzys-cafe.json` feed; **Weather**, current conditions from Open-Meteo;
**Subway**, live next-train times from the MTA's realtime feeds; and **Bus**, live
next-bus times from MTA Bus Time.

---

## Quickstart on a Mac (no hardware needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
make install                 # or: pip install -r requirements.txt

# Easiest: the preview script (creates the venv on first run):
./preview.sh                 # live browser preview at http://localhost:8888
./preview.sh png             # render one static frame to a PNG and open it

# Or use the make targets — live preview serves at http://localhost:8888:
make dev                     # full app rotation
make dev APP=weather         # just one app — handy while building it
make frames APP=weather      # render frames to PNGs, headless -> frames/weather/
```

`make dev`/`make frames` are thin wrappers over `python run.py` (use it directly if
you prefer: `python run.py --app weather`, `python run.py --dump-frames frames/`).

Before pushing, run the green gate — byte-compiles everything and runs a hardware-free
smoke test of the kernel and system layer. CI runs exactly this:

```bash
./dev/check.sh
```

The `--led-*` flags mirror `rpi-rgb-led-matrix`, e.g.
`python run.py --led-rows=64 --led-cols=64 --led-chain=2`. Panel geometry defaults
live in `config.yaml`.

### Emulator look

`emulator_config.json` tunes how the browser emulator renders. It ships configured to
**simulate two chained Adafruit 64×64 3mm-pitch (P3) panels** — a 128×64 canvas drawn
with the `real` pixel style plus a subtle glow, so previews resemble the physical sign
lit up. Panel geometry (rows/cols/chain) lives in `config.yaml`; this file only
controls appearance (pixel size/style/glow).

## Architecture

Apps live in this repo (a monorepo) behind a clean kernel/app seam. For *why* — and
when to revisit it — see [ADR 0001](docs/adr/0001-monorepo-with-clean-seam.md); the
seam rules for app authors live in `apps/__init__.py`.

```
run.py            entrypoint + CLI (--led-* flags, --dump-frames)
config.yaml       matrix geometry, app rotation, dwell/transition
kernel/
  display.py      Mac/Pi drop-in shim + matrix construction
  launcher.py     rotate apps, double-buffered frame loop, transitions
  app.py          the App base class (the whole app contract)
  loader.py       discover + instantiate apps from apps/<name>/
  data.py         cached, stale-on-error fetching — JSON or bytes (http + file)
  render.py       font loading + text/scroll helpers
  services.py     the handle passed to each app
  input.py        (optional) HAT GPIO buttons -> next/prev app
apps/
  cafe_menu/      the site menu, scrolling
  weather/        current conditions + high/low, procedural weather icons
  subway/         next trains per station, incl. a tiny GTFS-realtime reader
fonts/            drop a .ttf/.otf here for crisp text
```

An app implements one method:

```python
class MyApp(App):
    def refresh(self):        # pull data off the render loop (optional)
        ...
    def render(self, t):      # return a PIL.Image for elapsed time t (seconds)
        ...
```

## Adding an app

1. `mkdir apps/weather` with an `app.py` defining one `App` subclass and a
   `manifest.yaml` (name, `dwell`, `refresh_interval`, any config keys).
2. Add `weather` to `launcher.rotation` in `config.yaml`.

That's it — the loader discovers it, the launcher rotates to it. Fetch data via
`self.services.data.get_json(url, ttl=...)` — or `get_bytes(...)` for a binary feed,
which is how the subway app reads the MTA's protobuf. Both cache with a TTL and keep
showing the last good value if a fetch fails.

## Deploying to the Raspberry Pi

Flash an SD card and the sign does the rest. No monitor, no keyboard, no SSH.

```bash
./tools/flash.sh                                        # interactive
./tools/flash.sh --device disk6 --ssid Home --hostname lobby-sign
```

`flash.sh` downloads and SHA256-verifies the latest **Raspberry Pi OS Lite arm64**
(cached in `~/Library/Caches`), writes it to the card, and injects the HUB75 hardware
config, your WiFi credentials + SSH key, and the first-boot payload. It refuses
internal disks and makes you retype the device and its size before erasing anything.

Use a **Pi 4 / 3B+ / Zero 2 W** — they have solid GPIO timing.

### First boot (~10 minutes, unattended)

Provision (hostname, user, SSH key, WiFi profile) → reboot → compile the matrix driver
into `/opt/dizzyos/venv` → install the latest release → start. Then the sign is up and
rotating apps on its own. Watch progress by SSHing in if you like, or just wait for
pixels.

While the network is down the sign draws a small **red no-WiFi icon** in the top-left
corner, over whatever app is on screen. That's the one thing to look for if a sign
comes up blank-ish or stale.

### Settings page

Every sign serves a settings page on the LAN:

```
http://<hostname>.local:8080
```

Opening it makes the sign **display a one-time PIN for 30 seconds** — type what you see
on the sign to unlock the session. That's the authorization model: you have to be in
the room. From there you can join a different WiFi network and edit the device's
`config.yaml` (validated on save; the sign restarts itself to apply).

On-device config lives at **`/etc/dizzyos/config.yaml`** — outside the release tree, so
it survives every update.

### Self-updating

A timer polls for the latest GitHub release every five minutes (ETag-conditional, so
an unchanged answer is a rate-limit-free 304 — a new release reaches signs within
minutes of the tag landing). When one appears, the updater unpacks its
source tarball to `/opt/dizzyos/releases/<tag>/`, and atomically flips the
`/opt/dizzyos/current` symlink. The launcher touches a heartbeat file every frame, so
the updater can tell whether the new release actually renders — if it doesn't, the
symlink flips back, the tag is marked bad, and the sign keeps running what it had.
Releases carry no build artifacts by design (see
[ADR 0002](docs/adr/0002-flash-provision-update.md)).

Run one by hand, or read the log:

```bash
sudo dizzyos-update            # update if there's a newer release
sudo dizzyos-update --force    # re-deploy the current tag, or retry a bad one
journalctl -u dizzyos-update
```

### Hardware notes

- **Power:** two 64×64 panels can pull ~3–4A **each** at full white (~8A peak). Run
  **two 5V supplies**: one to the Pi, and a separate **5V 8–10A** supply to the HAT's
  screw terminal, which feeds the panels — don't run the panels off the Pi's supply.
  Fuse the panel supply and keep the grounds common. (Full wiring: the build sheet.)
- **Solder the HAT's address-E jumper** (required for 1:32-scan 64-row panels).
- **Solder the hardware-PWM jumper** (GPIO4↔GPIO18) to kill flicker, then use
  `--led-gpio-mapping=adafruit-hat-pwm`.
- **Stack vs. side-by-side:** the panels chain into 128×64. To mount them as a tall
  64×128 instead, set `pixel_mapper_config: "Rotate:90"` in `config.yaml` — no code
  change.
- **Enclosure + full build:** [`docs/build-sheet.html`](docs/build-sheet.html) is the
  step-by-step wood/acrylic build (cut list, wiring, assembly); the parametric model
  and its fit checks live in [`cad/`](cad/) (`python cad/test_fit.py`).

## Credits

Built on [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) and
[RGBMatrixEmulator](https://github.com/ty-porter/RGBMatrixEmulator). Architecture nods
to the [MLB](https://github.com/MLB-LED-scoreboard/mlb-led-scoreboard)/[NHL](https://github.com/riffnshred/nhl-led-scoreboard)
LED scoreboards, which use the same stack.
