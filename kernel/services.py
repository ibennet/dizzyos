"""Services — the handle the kernel passes to each app on start.

Bundles the shared facilities an app needs: canvas dimensions, the cached data
fetcher, the font book, and a logger. Keeping these behind one object means apps
don't reach into kernel internals and stay easy to test with fakes.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .data import DataService
from .render import FontBook


@dataclass
class Services:
    width: int
    height: int
    data: DataService
    fonts: FontBook
    log: Callable[[str], None]
    #: The NetworkMonitor, if one is running, so apps can read `net.online`
    #: (e.g. to dim stale data). Typed loosely to avoid importing netmon here.
    net: Optional[Any] = None
