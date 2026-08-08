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
        _apply_tuning(options, m)

    return RGBMatrix(options=options)


#: Optional rpi-rgb-led-matrix refresh knobs, config key -> option attribute.
#: These are how you trade color depth for refresh rate, which is the lever
#: that matters for visible flicker — especially on a board without the
#: hardware-PWM jumper, where the driver pulses in software and any timing
#: jitter shows up on the panel. Unset means "leave the library's default".
_TUNING = {
    "pwm_bits": "pwm_bits",                          # 11 default; lower = faster refresh
    "pwm_lsb_nanoseconds": "pwm_lsb_nanoseconds",    # 130 default; lower = faster refresh
    "pwm_dither_bits": "pwm_dither_bits",            # trades depth for refresh, subtler
    "limit_refresh_rate_hz": "limit_refresh_rate_hz",  # pin the rate for steadiness
    "scan_mode": "scan_mode",                        # 0 progressive, 1 interlaced
}


def _apply_tuning(options, matrix_cfg):
    """Copy any refresh-tuning keys present in config onto the matrix options."""
    for key, attr in _TUNING.items():
        if key in matrix_cfg:
            setattr(options, attr, matrix_cfg[key])
