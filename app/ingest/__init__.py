"""The ingestion feature.

The blueprint is exposed here so the application factory can register it
without importing the route module directly.
"""

from .routes import bp

__all__ = ["bp"]
