"""The raw point browser feature.

The blueprint is exposed here so the application factory can register the
raw API without importing its route module in every other package.
"""

from __future__ import annotations

from .routes import bp

__all__ = ["bp"]
