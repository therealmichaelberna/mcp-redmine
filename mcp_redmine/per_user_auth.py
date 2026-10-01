"""
Per-user Redmine API-key support for centrally-hosted (multi-user) deployments.

=============================================================================
FORK ADDITION — this entire module is NOT part of upstream runekaagaard/mcp-redmine.
See FORK.md for the rationale and the upstream-sync procedure.
=============================================================================

Upstream mcp-redmine authenticates every Redmine call with a single
``REDMINE_API_KEY`` read from the process environment at import time. That is
correct for the upstream use case (a local, single-user stdio server launched
by one person's MCP client).

For a centrally-hosted HTTP deployment (one server process handling requests
from many users), a single shared key would make every user act as one Redmine
service account — losing per-user attribution and permission scoping.

This module lets each incoming HTTP request carry the caller's own Redmine API
key in an ``X-Redmine-API-Key`` header. A Starlette/ASGI middleware copies that
header value into a :class:`contextvars.ContextVar` for the duration of the
request; :func:`server.request` then prefers the context key over the env key.

Design goals (keep upstream rebases painless):
- Additive: all per-user logic lives in THIS file. ``server.py`` changes are
  minimal (make the env key optional, prefer the context key, wire middleware).
- Safe fallback: when no header is present (e.g. stdio transport, or a client
  that doesn't send it), behaviour is identical to upstream — the env key is
  used.
- No new dependencies: Starlette ships with the ``mcp`` package already.
"""

from contextvars import ContextVar
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

# Header the client sends the caller's personal Redmine API key in.
PER_USER_API_KEY_HEADER = "x-redmine-api-key"

# Holds the current request's Redmine API key (None when not set → env fallback).
_current_api_key: ContextVar[Optional[str]] = ContextVar("redmine_api_key", default=None)


def get_request_api_key() -> Optional[str]:
    """Return the API key for the current request, or None if none was supplied."""
    return _current_api_key.get()


class PerUserApiKeyMiddleware:
    """
    ASGI middleware that captures the ``X-Redmine-API-Key`` request header into
    a ContextVar so downstream tool calls authenticate as the calling user.

    Pure-ASGI (not BaseHTTPMiddleware) so it works with streaming/SSE responses
    without buffering. Only touches HTTP scopes; other scopes pass through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_key: Optional[str] = None
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.decode("latin-1").lower() == PER_USER_API_KEY_HEADER:
                api_key = raw_value.decode("latin-1").strip() or None
                break

        token = _current_api_key.set(api_key)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_api_key.reset(token)
