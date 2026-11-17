"""The query feature.

The blueprint is exposed lazily so the pure helpers of ``app.query.service``
can be imported (and unit tested) without dragging Flask in.  The route
module is only loaded when the blueprint is actually requested.
"""

from __future__ import annotations

from typing import Any

__all__ = ["bp"]


def __getattr__(name: str) -> Any:
    """Expose the blueprint without importing the route module eagerly."""
    if name == "bp":
        from .routes import bp

        return bp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
