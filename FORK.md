# FORK.md — fork of `runekaagaard/mcp-redmine`

This repository is a fork of [`runekaagaard/mcp-redmine`](https://github.com/runekaagaard/mcp-redmine),
adding **per-user API-key** support for centrally-hosted (multi-user)
deployments.

## Why this fork exists

Upstream authenticates every Redmine call with a **single** `REDMINE_API_KEY`
read from the process environment at startup. That is correct for upstream's
target use case: a local, single-user server launched by one person's MCP client
over stdio.

This fork targets a different deployment: **one centrally-hosted server process
handling requests from many users**. A single shared key would make every user
act as one Redmine service account, destroying:

- **Attribution** — every ticket/comment/time entry would be authored by one account.
- **Permission scoping** — every user would inherit that one account's Redmine rights.

This fork adds **per-user API keys**: each incoming HTTP request carries the
caller's own Redmine API key in an `X-Redmine-API-Key` header, and the server
uses that key for the Redmine calls made while handling that request.

Upstream declined to engage with this multi-user case
(see upstream issue #34 "X-Redmine-Switch-User", closed without resolution), so
the capability is maintained here.

## Exactly what we changed

The change is deliberately small and **additive** to keep upstream rebases easy.

### 1. New file: `mcp_redmine/per_user_auth.py` (additive — no upstream conflict)

- `PER_USER_API_KEY_HEADER` — the identity header name (`x-redmine-api-key`).
- `PER_USER_PASSTHROUGH_HEADERS` — request headers forwarded verbatim to
  Redmine (currently `x-redmine-username`). Some deployments front Redmine with
  a gateway that *requires* `X-Redmine-Username` to be present for the request
  to be routed/accepted; the API key still determines identity.
- `_current_api_key` / `_current_passthrough` ContextVars — hold the current
  request's key and passthrough headers.
- `get_request_api_key()` / `get_request_passthrough_headers()` — read them.
- `PerUserApiKeyMiddleware` — pure-ASGI middleware that copies the
  `X-Redmine-API-Key` and passthrough headers into the ContextVars for the
  request's lifetime and resets them afterwards. Pure-ASGI (not
  `BaseHTTPMiddleware`) so it does not buffer streaming/SSE responses.

### 2. `mcp_redmine/server.py` — three minimal, clearly-marked edits

Every fork edit is tagged with a `# FORK:` comment so it is greppable
(`grep -n "FORK" mcp_redmine/server.py`).

| Location | Upstream | Fork |
| --- | --- | --- |
| import block | (none) | `from mcp_redmine.per_user_auth import get_request_api_key` |
| env constants | `REDMINE_API_KEY = os.environ['REDMINE_API_KEY']` | `REDMINE_API_KEY = os.environ.get('REDMINE_API_KEY', '')` (now optional) |
| `request()` header | `'X-Redmine-API-Key': REDMINE_API_KEY` | prefer `get_request_api_key()`, fall back to env; error if neither |
| `main()` HTTP branch | `mcp.run(transport=..., host=..., port=...)` | build app via `mcp.streamable_http_app()`/`sse_app()`, `add_middleware(PerUserApiKeyMiddleware)`, run with `uvicorn.run(...)` |

**Why `main()` builds the app manually:** upstream's `mcp.run()` /
`run_streamable_http_async()` rebuild the Starlette app internally, which drops
any middleware added to an externally-obtained app. Building the app ourselves
and serving it with uvicorn is the only way to attach the middleware. If a
future upstream version adds a supported middleware hook, prefer that and drop
this workaround.

## Behaviour / compatibility

- **stdio transport:** unchanged. No header exists; the env `REDMINE_API_KEY`
  fallback is used exactly as upstream.
- **HTTP transports (streamable-http / sse):** if the request includes
  `X-Redmine-API-Key`, that key is used; otherwise the env key is used; if
  neither exists, tool calls return a clear error (no HTTP call is made).
- **No new dependencies.** Starlette and uvicorn already ship with `mcp[cli]`.

## Client requirement

The MCP client must send the per-user key as an `X-Redmine-API-Key` request
header on each call, sourcing it from wherever that user's key is stored. If the
Redmine deployment is fronted by a gateway that requires it (e.g.
`api-cdredmine*`), the client must also send an `X-Redmine-Username` header; it
is forwarded verbatim to Redmine.

## How to verify after any change or sync

```sh
python3 -m venv .venv-test
.venv-test/bin/pip install "mcp[cli]==2.1.1" pyyaml httpx uvicorn

# Unit-level: env fallback, per-user override, no-key error, middleware capture
# (see the checks in the project's CI / the commit that introduced this fork).

# Integration smoke: server boots and answers initialize on /mcp
REDMINE_URL="https://redmine.example.com" \
  .venv-test/bin/python -m mcp_redmine.server \
  --transport streamable-http --host 127.0.0.1 --port 18099 &
curl -s -X POST http://127.0.0.1:18099/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-Redmine-API-Key: TESTKEY' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
# Expect HTTP 200 with a JSON-RPC result containing serverInfo.
rm -rf .venv-test
```

## Keeping in sync with upstream

Remotes (already configured in this clone):

```sh
git remote -v
# origin    git@github.com:therealmichaelberna/mcp-redmine.git   (fetch/push)
# upstream  git@github.com:runekaagaard/mcp-redmine.git           (fetch)
# upstream push is intentionally disabled (set to DISABLE_PUSH_TO_UPSTREAM)
```

### Rebase workflow (preferred — keeps our 1 commit on top)

```sh
git fetch upstream
git checkout main
git rebase upstream/main
# Conflicts, if any, will only be in server.py at the `# FORK:` lines above.
# Resolve by re-applying the four small edits (the table is the source of truth).
# per_user_auth.py is additive and will never conflict.

# Re-run the verification steps above, then:
git push --force-with-lease origin main
```

### Merge workflow (alternative — if you prefer merge commits)

```sh
git fetch upstream
git checkout main
git merge upstream/main
# resolve any server.py `# FORK:` conflicts, verify, then:
git push origin main
```

### If a conflict is confusing

The whole fork is 4 small edits + 1 new file. Worst case, reset to upstream and
re-apply from the table above:

```sh
git fetch upstream
git checkout -B main upstream/main
# re-apply the four server.py edits (see table) and keep per_user_auth.py
```

## Upstreaming status

Not upstreamed. Upstream issue #34 (the same multi-user question) was closed
without engagement, and the project is framed as single-user, so a multi-user
auth PR is likely out of scope. If you attempt it, open an issue first
referencing the centrally-hosted use case and `jztan/redmine-mcp-server#261`
(prior art that this is a common need), and offer the middleware as an opt-in.
Until accepted, treat this fork as long-lived and keep this document current.
