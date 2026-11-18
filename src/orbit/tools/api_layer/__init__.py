"""API layer: official integrations (Spotify, GitHub, ...). Most reliable, so the router prefers it.

Empty on purpose until Step 4. To add a connector later: write Tool(..., layer="api", capability="<same
capability as the os/browser tool it replaces>") and register it in register_api_tools(). No other file changes.
"""
from ..base import Registry


def register_api_tools(registry: Registry) -> None:
    """No connectors yet."""
