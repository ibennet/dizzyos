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

import yaml

from kernel import display as display_mod
from kernel.data import DataService
from kernel.loader import load_app
from kernel.render import FontBook
from kernel.services import Services

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


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


def build_services(cfg, log):
    width, height = display_mod.canvas_size(cfg)
    fonts = FontBook(os.path.join(ROOT, "fonts"))
    return Services(width=width, height=height, data=DataService(log=log), fonts=fonts, log=log)


def rotation_apps(cfg):
    names = cfg.get("launcher", {}).get("rotation", [])
    apps_cfg = cfg.get("apps", {})
    return [load_app(name, apps_cfg.get(name, {})) for name in names]


def dump_frames(cfg, args, log):
    services = build_services(cfg, log)
    names = cfg.get("launcher", {}).get("rotation", [])
    name = args.app or (names[0] if names else None)
    if not name:
        sys.exit("nothing to render: no --app given and rotation is empty")

    app_cfg = dict(cfg.get("apps", {}).get(name, {}))
    if args.menu_url:
        app_cfg["menu_url"] = args.menu_url
    app = load_app(name, app_cfg)
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
    parser.add_argument("--app", help="app to render in --dump-frames mode (default: first in rotation)")
    parser.add_argument("--frames", type=int, default=12, help="frame count for --dump-frames")
    parser.add_argument("--duration", type=float, default=8.0, help="seconds of animation to span")
    parser.add_argument("--menu-url", help="override the cafe_menu feed URL/path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    apply_overrides(cfg, args)

    def log(message):
        print(message, file=sys.stderr)

    if args.dump_frames:
        dump_frames(cfg, args, log)
        return

    from kernel.launcher import Launcher  # deferred: pulls in the matrix library

    matrix = display_mod.create_matrix(cfg)
    apps = rotation_apps(cfg)
    log(f"dizzyos up: {display_mod.canvas_size(cfg)} canvas, apps={[a.name for a in apps]}")
    try:
        Launcher(matrix, apps, cfg, build_services(cfg, log)).run()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()
