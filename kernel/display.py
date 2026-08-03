"""Matrix construction and the Mac/Pi drop-in shim.

The single `try/except` below is the whole cross-platform trick: on the Raspberry Pi
the real `rgbmatrix` module (from hzeller/rpi-rgb-led-matrix) is installed; on a Mac
only `RGBMatrixEmulator` is, and it exposes the same class names. Nothing else in the
codebase branches on platform.
"""

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions  # real Pi library
    IS_HARDWARE = True
except ImportError:  # pragma: no cover - depends on install target
    from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions  # Mac emulator
    IS_HARDWARE = False


def canvas_size(cfg):
    """Return (width, height) of the full chained canvas in pixels."""
    m = cfg["matrix"]
    return m["cols"] * m.get("chain", 1), m["rows"] * m.get("parallel", 1)


def create_matrix(cfg):
    """Build an RGBMatrix from the `matrix:` block of the config.

    Hardware-only options (GPIO mapping/slowdown) are applied only on the Pi; the
    emulator ignores them, but we keep them behind the flag so the intent is clear.
    """
    m = cfg["matrix"]
    options = RGBMatrixOptions()
    options.rows = m["rows"]
    options.cols = m["cols"]
    options.chain_length = m.get("chain", 1)
    options.parallel = m.get("parallel", 1)
    options.brightness = m.get("brightness", 70)

    mapper = m.get("pixel_mapper_config")
    if mapper:
        # e.g. "Rotate:90" or "V-mapper" to stack the two panels as a tall 64x128.
        options.pixel_mapper_config = mapper

    if IS_HARDWARE:
        options.hardware_mapping = m.get("hardware_mapping", "adafruit-hat")
        options.gpio_slowdown = m.get("gpio_slowdown", 4)
        options.drop_privileges = m.get("drop_privileges", True)

    return RGBMatrix(options=options)
