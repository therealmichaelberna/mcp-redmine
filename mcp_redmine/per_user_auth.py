"""
Per-user Redmine auth headers for centrally-hosted (multi-user) deployments.

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

This module lets each incoming HTTP request carry per-user auth headers that
are forwarded to Redmine for that request only. Two headers are supported:

- ``X-Redmine-API-Key`` — the caller's personal Redmine API key (their
  identity). Preferred over the env ``REDMINE_API_KEY`` fallback.
- ``X-Redmine-Username`` — forwarded verbatim to Redmine. Some deployments
  front Redmine with a gateway that *requires* this header to be present for
  the request to be routed/accepted (the API key still determines identity).

A Starlette/ASGI middleware copies these request headers into ContextVars for
the duration of the request; :func:`server.request` then uses them.

Design goals (keep upstream rebases painless):
- Additive: all per-user logic lives in THIS file. ``server.py`` changes are
  minimal (make the env key optional, prefer the context key, forward the
  per-user passthrough headers, wire middleware).
- Safe fallback: when no header is present (e.g. stdio transport, or a client
  that doesn't send it), behaviour is identical to upstream — the env key is
  used and no extra headers are added.
- No new dependencies: Starlette ships with the ``mcp`` package already.
"""

from contextvars import ContextVar
from typing import Dict, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

# Request header carrying the caller's personal Redmine API key (their identity).
PER_USER_API_KEY_HEADER = "x-redmine-api-key"

# Request headers forwarded verbatim to Redmine (gateway/passthrough headers).
# Lower-case names; mapped to their canonical outbound form in OUTBOUND_HEADER_NAMES.
PER_USER_PASSTHROUGH_HEADERS = ("x-redmine-username",)

# Canonical (outbound) header names to send to Redmine.
OUTBOUND_HEADER_NAMES = {
    "x-redmine-username": "X-Redmine-Username",
}

# Holds the current request's Redmine API key (None when not set → env fallback).
_current_api_key: ContextVar[Optional[str]] = ContextVar("redmine_api_key", default=None)

# Holds the current request's passthrough headers (canonical name → value).
_current_passthrough: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "redmine_passthrough_headers", default=None
)


def get_request_api_key() -> Optional[str]:
    """Return the API key for the current request, or None if none was supplied."""
    return _current_api_key.get()


def get_request_passthrough_headers() -> Dict[str, str]:
    """Return the per-request passthrough headers (canonical name → value)."""
    return dict(_current_passthrough.get() or {})


class PerUserApiKeyMiddleware:
    """
    ASGI middleware that captures per-user Redmine auth headers from the request
    into ContextVars so downstream tool calls authenticate/route as the caller:

    - ``X-Redmine-API-Key`` → identity (preferred over the env key)
    - ``X-Redmine-Username`` → forwarded verbatim (gateway passthrough)

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
        passthrough: Dict[str, str] = {}
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1").strip()
            if name == PER_USER_API_KEY_HEADER:
                api_key = value or None
            elif name in PER_USER_PASSTHROUGH_HEADERS and value:
                passthrough[OUTBOUND_HEADER_NAMES[name]] = value

        key_token = _current_api_key.set(api_key)
        pass_token = _current_passthrough.set(passthrough or None)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_api_key.reset(key_token)
            _current_passthrough.reset(pass_token)
