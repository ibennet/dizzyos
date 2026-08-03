"""App discovery: turn an app name into a configured App instance.

Convention: every app lives in `apps/<name>/` with an `app.py` that defines exactly
one `App` subclass and a `manifest.yaml` of defaults. The manifest is merged under
the per-app config from `config.yaml` (config wins), so users can override dwell,
refresh interval, URLs, etc. without touching code.
"""

import importlib
import inspect
import os

import yaml

from .app import App

APPS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "apps")


def load_manifest(name):
    path = os.path.join(APPS_DIR, name, "manifest.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_app(name, user_config=None):
    """Import `apps.<name>.app`, find its App subclass, and instantiate it."""
    module = importlib.import_module(f"apps.{name}.app")
    app_classes = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, App) and obj is not App and obj.__module__ == module.__name__
    ]
    if len(app_classes) != 1:
        raise RuntimeError(
            f"app '{name}' must define exactly one App subclass, found {len(app_classes)}"
        )

    config = {**load_manifest(name), **(user_config or {})}
    instance = app_classes[0](config=config)
    instance.name = config.get("name", name)
    return instance
