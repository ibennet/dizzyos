#!/usr/bin/env python3
"""dizzyos entrypoint.

Same command drives the emulator on a Mac and the real panels on a Pi — the only
difference is which matrix library is installed (see kernel/display.py). The
`--led-*` flags mirror rpi-rgb-led-matrix so muscle memory carries over.

Headless dev/CI: `--dump-frames DIR` renders an app's frames to PNGs without any
display, so you can eyeball the output (or diff it) before wiring up hardware.
"""

import argparse
import os
import sys
import threading

import yaml

from kernel import display as display_mod
from kernel.data import DataService
from kernel.loader import APPS_DIR, load_app
from kernel.render import FontBook
from kernel.services import Services

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def validate_config(cfg):
    """Return None if `cfg` is safe to boot, else a human-readable reason.

    Walks the same paths run.py uses — canvas sizing and rotation app lookup —
    so the LAN settings page (which injects this as its validator) cannot save a
    config that would crash the launcher and, with `Restart=always`, spin the
    sign in a restart loop with no way back in.
    """
    if not isinstance(cfg, dict) or "matrix" not in cfg:
        return "this doesn't look like a dizzyos config"
    m = cfg["matrix"]
    if not isinstance(m, dict):
        return "matrix: must be a mapping"
    for key in ("rows", "cols"):
        if not isinstance(m.get(key), int) or m[key] <= 0:
            return f"matrix.{key} must be a positive integer"
    for key in ("chain", "parallel"):
        if key in m and (not isinstance(m[key], int) or m[key] <= 0):
            return f"matrix.{key} must be a positive integer"
    try:
        display_mod.canvas_size(cfg)
    except (KeyError, TypeError) as exc:
        return f"matrix block is invalid ({exc})"
    rotation = (cfg.get("launcher") or {}).get("rotation", [])
    if not isinstance(rotation, list) or not rotation:
        return "launcher.rotation must list at least one app"
    for name in rotation:
        if not os.path.isfile(os.path.join(APPS_DIR, str(name), "app.py")):
            return (f"launcher.rotation names unknown app '{name}' "
                    f"(have: {', '.join(available_apps()) or 'none'})")
    return None


def load_config_with_fallback(path, log, validate=True):
    """Load config, falling back to the `.prev` copy the settings page keeps if
    the primary file is missing/corrupt/unbootable. A power cut mid-save, or a
    hand-edit that doesn't boot, then costs a reboot to the last good config
    rather than a re-flash."""
    try:
        cfg = load_config(path)
        if validate:
            reason = validate_config(cfg)
            if reason:
                raise ValueError(reason)
        return cfg
    except Exception as exc:  # noqa: BLE001 - any load/parse/validate failure
        prev = path + ".prev"
        if os.path.exists(prev):
            log(f"config: {path} unusable ({exc}); falling back to {prev}")
            return load_config(prev)
        raise


def apply_overrides(cfg, args):
    m = cfg["matrix"]
    if args.led_rows:
        m["rows"] = args.led_rows
    if args.led_cols:
        m["cols"] = args.led_cols
    if args.led_chain:
        m["chain"] = args.led_chain
    if args.led_parallel:
        m["parallel"] = args.led_parallel
    if args.led_brightness is not None:
        m["brightness"] = args.led_brightness
    if args.led_pixel_mapper:
        m["pixel_mapper_config"] = args.led_pixel_mapper


def build_services(cfg, log, fonts=None):
    width, height = display_mod.canvas_size(cfg)
    fonts = fonts or FontBook(os.path.join(ROOT, "fonts"))
    return Services(width=width, height=height, data=DataService(log=log), fonts=fonts, log=log)


def available_apps():
    """Names of apps discoverable on disk (a dir under apps/ with an app.py)."""
    return sorted(
        name for name in os.listdir(APPS_DIR)
        if os.path.isfile(os.path.join(APPS_DIR, name, "app.py"))
    )


def load_named_app(cfg, name):
    """Load one app by name, merging its config.yaml overrides over its manifest defaults.

    Exits with a clear message (not a traceback) when `name` isn't a real app — e.g. a
    typo in `--app` or a bad entry in the rotation.
    """
    if not os.path.isfile(os.path.join(APPS_DIR, name, "app.py")):
        sys.exit(f"no such app '{name}'. available: {', '.join(available_apps()) or '(none)'}")
    return load_app(name, cfg.get("apps", {}).get(name, {}))


def selected_names(cfg, args):
    """App names to run: just `--app` if given (dev preview), else the config rotation."""
    if args.app:
        return [args.app]
    return cfg.get("launcher", {}).get("rotation", [])


def select_apps(cfg, args):
    """Load the apps to run live: a single `--app`, or the full rotation."""
    return [load_named_app(cfg, name) for name in selected_names(cfg, args)]


def select_apps_safe(cfg, args, log):
    """Like select_apps, but a bad/missing app is logged and skipped rather than
    calling sys.exit — the live launcher must not die (and restart-loop) over one
    typo'd rotation entry. Returns whatever loaded; the launcher renders its own
    fallback frame if that's nothing, keeping the settings page reachable."""
    apps = []
    for name in selected_names(cfg, args):
        if not os.path.isfile(os.path.join(APPS_DIR, name, "app.py")):
            log(f"launcher: skipping unknown app '{name}'")
            continue
        try:
            apps.append(load_app(name, cfg.get("apps", {}).get(name, {})))
        except Exception as exc:  # noqa: BLE001 - one app's failure isn't fatal
            log(f"launcher: failed to load '{name}': {exc}")
    return apps


def dump_frames(cfg, args, log):
    services = build_services(cfg, log)
    names = selected_names(cfg, args)
    if not names:
        sys.exit("nothing to render: no --app given and rotation is empty")

    app = load_named_app(cfg, names[0])
    app.on_start(services)
    app.refresh()

    os.makedirs(args.dump_frames, exist_ok=True)
    for i in range(args.frames):
        t = (i / max(args.frames - 1, 1)) * args.duration
        frame = app.render(t).convert("RGB")
        out = os.path.join(args.dump_frames, f"frame_{i:03d}.png")
        frame.save(out)
        log(f"wrote {out}  (t={t:5.1f}s  {frame.width}x{frame.height})")
    app.on_stop()


def main():
    parser = argparse.ArgumentParser(description="dizzyos — a mini OS for LED matrix panels")
    parser.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    # rpi-rgb-led-matrix-style overrides (optional; config.yaml supplies defaults).
    parser.add_argument("--led-rows", type=int)
    parser.add_argument("--led-cols", type=int)
    parser.add_argument("--led-chain", type=int)
    parser.add_argument("--led-parallel", type=int)
    parser.add_argument("--led-brightness", type=int)
    parser.add_argument("--led-pixel-mapper", help='e.g. "Rotate:90" to stack panels as 64x128')
    # Headless render mode.
    parser.add_argument("--dump-frames", metavar="DIR",
                        help="render frames to PNGs instead of driving a matrix")
    parser.add_argument("--app", help="run/render a single app instead of the full rotation "
                                      "(great for dev preview); default: the config rotation, "
                                      "or its first app in --dump-frames mode")
    parser.add_argument("--frames", type=int, default=12, help="frame count for --dump-frames")
    parser.add_argument("--duration", type=float, default=8.0, help="seconds of animation to span")
    parser.add_argument("--menu-url", help="override the cafe_menu feed URL/path")
    args = parser.parse_args()

    def log(message):
        print(message, file=sys.stderr)

    # A live boot validates and can fall back to the last-good `.prev` config; a
    # single-app dev preview (--app) skips that so a throwaway config still runs.
    cfg = load_config_with_fallback(args.config, log, validate=not args.app)
    apply_overrides(cfg, args)
    if args.menu_url:  # applies to both the live launcher and --dump-frames
        cfg.setdefault("apps", {}).setdefault("cafe_menu", {})["menu_url"] = args.menu_url

    if args.dump_frames:
        dump_frames(cfg, args, log)
        return

    from kernel import __version__
    from kernel.launcher import Launcher  # deferred: pulls in the matrix library
    from kernel.netmon import NetworkMonitor
    from kernel.overlay import OverlayManager
    from kernel.settings import SettingsServer

    # System layer first — before the matrix and apps. The status overlays and
    # the PIN-gated LAN settings page must come up even if the display or an app
    # fails to build, so a bad config is always recoverable over the network
    # rather than by pulling the SD card. Each is opt-out via `system:` in config.
    fonts = FontBook(os.path.join(ROOT, "fonts"))
    overlays = OverlayManager()
    system = cfg.get("system") or {}
    if system.get("network_monitor", True):
        NetworkMonitor(overlays, log).start()
    settings_cfg = system.get("settings") or {}
    if settings_cfg.get("enabled", True):
        try:
            SettingsServer(args.config, overlays, fonts, log,
                           version=__version__,
                           port=settings_cfg.get("port", 8080),
                           validate=validate_config).start()
        except OSError as exc:  # port taken (e.g. second dev instance) — not fatal
            log(f"settings: disabled ({exc})")

    try:
        matrix = display_mod.create_matrix(cfg)
    except Exception as exc:  # noqa: BLE001 - hold, don't exit into a restart loop
        log(f"display: could not build the matrix ({exc}); holding with the "
            "settings page up so the config can be fixed over the network")
        threading.Event().wait()
        return

    services = build_services(cfg, log, fonts=fonts)
    apps = select_apps_safe(cfg, args, log)
    log(f"dizzyos up: {display_mod.canvas_size(cfg)} canvas, apps={[a.name for a in apps]}")
    try:
        Launcher(matrix, apps, cfg, services, overlays=overlays).run()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()
