"""dizzyos kernel — the shared runtime that hosts apps on the LED matrix.

The kernel owns everything hardware- and platform-specific (the matrix, the frame
loop, data fetching, fonts) so that apps stay small, portable, and testable. An app
only implements `render(t) -> PIL.Image`; the kernel does the rest.
"""

__version__ = "0.0.0"  # x-release-please-version
