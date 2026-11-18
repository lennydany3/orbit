"""API layer: official integrations (Spotify so far). Most reliable, so the router prefers it.

To add a connector: write Tool(..., layer="api", capability="<same capability as the os/browser tool it
replaces, if any>") and register it in register_api_tools(). No other file changes.
"""
from ..base import Registry
from . import spotify_auth
from .spotify import register_spotify_tools


def register_api_tools(registry: Registry) -> None:
    """Connectors only appear when they are configured, so the model never sees tools that can't work."""
    if spotify_auth.client_id_from_env():
        register_spotify_tools(registry)
