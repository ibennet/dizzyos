"""dizzyos apps — self-contained plugins the kernel rotates through on the panel.

Each app is a package under ``apps/<name>/`` with an ``app.py`` defining exactly one
``App`` subclass (enforced by ``kernel/loader.py``) and a ``manifest.yaml`` of defaults
(name, ``dwell``, ``refresh_interval``, any config keys). The loader discovers it and
the launcher rotates to it — see the top-level README's "Adding an app".

The kernel/app seam (keep it clean — it's what keeps apps portable and testable):

- Import from the kernel only the ``App`` base class (``kernel.app``) and the shared
  render helpers (``kernel.render``, e.g. ``glyph_height``). Everything else an app
  needs arrives on the ``services`` handle passed to ``on_start``: ``width``,
  ``height``, ``data``, ``fonts``, ``log`` (see ``kernel/services.py``).
- Never touch the matrix or canvas directly. ``render(t)`` returns a ``PIL.Image``
  sized to the canvas and the kernel blits it to the panels.
- Fetch data through ``services.data.get_json(url, ttl=...)`` and load fonts through
  ``services.fonts`` — don't reach into other kernel internals.

Why apps live in this repo rather than their own: see
``docs/adr/0001-monorepo-with-clean-seam.md``.
"""
