"""Local API adapter between the React frontend and the RippleBack MCP server.

Why this exists: browsers cannot speak the MCP Streamable HTTP protocol
directly (SSE framing, session headers, official-SDK handshake), and MCP
sessions must not be exposed to the browser anyway. This adapter is the
**single genuine MCP client** of the application: it owns one
:class:`rippleback.client.RippleBackMCPClient` (official Python MCP SDK)
and exposes a narrow, validated REST surface to the dashboard:

* ``GET  /api/health``        — adapter + MCP connection status, discovered tool names.
* ``GET  /api/mcp/tools``     — live-discovered tools with descriptions + input schemas.
* ``POST /api/mcp/tool``      — ``{tool, arguments}`` → structured result or a clear error.
* ``POST /api/assistant/chat``— ``{message, scenario_id?}`` → assistant reply + tool trace.

It binds to localhost only (override with ``RIPPLEBACK_ADAPTER_HOST``) and
keeps every credential (including AWS for the optional Bedrock provider) on
the backend. Business logic stays in the MCP server's engines; this adapter
only forwards, validates, and explains.

Start it with ``rippleback-adapter`` (or ``python -m rippleback.adapter``)
after starting the MCP server.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from rippleback.assistant.orchestrator import Assistant
from rippleback.assistant.providers import make_provider
from rippleback.client import (
    MCPConnectionError,
    MCPToolFailedError,
    MCPToolNotFoundError,
    RippleBackMCPClient,
)

DEFAULT_ADAPTER_HOST = "127.0.0.1"
DEFAULT_ADAPTER_PORT = 8001

ADAPTER_NAME = "rippleback-adapter"
ADAPTER_VERSION = "0.1.0"

_client: RippleBackMCPClient | None = None
_assistants: dict[str, Assistant] = {}


def get_client() -> RippleBackMCPClient:
    """Process-wide MCP client (one session, reused across requests)."""
    global _client
    if _client is None:
        _client = RippleBackMCPClient()
    return _client


def get_assistant(provider_name: str | None) -> Assistant:
    """Cached assistant per configured provider name."""
    selected = (provider_name or "default").strip().lower()
    if selected not in _assistants:
        provider, report = make_provider(provider_name)
        _assistants[selected] = Assistant(
            get_client(),
            provider=provider,
            provider_report=report,
            max_tool_calls=_max_tool_calls(),
        )
    return _assistants[selected]


def _max_tool_calls() -> int:
    raw = os.environ.get("RIPPLEBACK_MAX_TOOL_CALLS", "")
    try:
        value = int(raw) if raw else 8
    except ValueError:
        value = 8
    return max(1, value)


# ── Handlers ──────────────────────────────────────────────────────────────────


async def health(request: Request) -> JSONResponse:
    client = get_client()
    state = await client.health()
    provider, report = make_provider()
    tools: list[str] = []
    if state.connected:
        try:
            tools = [t.name for t in await client.list_tools()]
        except Exception:
            tools = []
    return JSONResponse(
        {
            # `name`/`version` describe the upstream MCP server the dashboard
            # is really talking to (kept for the existing frontend contract).
            "name": state.server_name or "RippleBack",
            "version": state.server_version or "unknown",
            "tools": tools,
            "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
            "mcp": state.to_dict(),
            "assistant": {
                "provider": report.provider,
                "fallback_used": report.fallback_used,
                "fallback_reason": report.fallback_reason,
            },
        },
        status_code=200 if state.connected else 503,
    )


async def list_tools(request: Request) -> JSONResponse:
    client = get_client()
    try:
        tools = await client.list_tools(refresh="refresh" in request.query_params)
    except MCPConnectionError as exc:
        return JSONResponse(
            {"error": str(exc), "isError": True, "tools": []}, status_code=503
        )
    return JSONResponse(
        {
            "connected": True,
            "count": len(tools),
            "tools": [t.to_dict() for t in tools],
        }
    )


async def call_tool(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be JSON", "isError": True}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object", "isError": True}, status_code=400)

    tool = body.get("tool")
    arguments = body.get("arguments", {})
    if not isinstance(tool, str) or not tool:
        return JSONResponse({"error": 'missing "tool" field', "isError": True}, status_code=400)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return JSONResponse({"error": '"arguments" must be an object', "isError": True}, status_code=400)

    client = get_client()
    try:
        result = await client.call_tool(tool, arguments)
    except MCPToolNotFoundError as exc:
        return JSONResponse({"error": str(exc), "isError": True}, status_code=404)
    except MCPToolFailedError as exc:
        # A genuine MCP tool error: reported as an error, never as success.
        return JSONResponse({"error": exc.message, "isError": True, "tool": tool}, status_code=200)
    except MCPConnectionError as exc:
        return JSONResponse({"error": str(exc), "isError": True}, status_code=503)
    return JSONResponse({"result": result, "isError": False})


async def chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": '"message" must be a non-empty string'}, status_code=400)
    scenario_id = body.get("scenario_id")
    if scenario_id is not None and not isinstance(scenario_id, str):
        return JSONResponse({"error": '"scenario_id" must be a string'}, status_code=400)
    provider_name = body.get("provider")
    if provider_name is not None and not isinstance(provider_name, str):
        return JSONResponse({"error": '"provider" must be a string'}, status_code=400)

    assistant = get_assistant(provider_name)
    response = await assistant.handle_message(message.strip(), scenario_id=scenario_id)
    return JSONResponse(response.to_dict())


@asynccontextmanager
async def lifespan(_app: Starlette):
    # No MCP connection at startup — it is established lazily on first use so
    # the dashboard (and adapter) can start before the MCP server is up.
    yield
    await get_client().disconnect()


# ── App factory ───────────────────────────────────────────────────────────────

_middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
]

app = Starlette(
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/mcp/tools", list_tools, methods=["GET"]),
        Route("/api/mcp/tool", call_tool, methods=["POST"]),
        Route("/api/assistant/chat", chat, methods=["POST"]),
    ],
    middleware=_middleware,
    lifespan=lifespan,
)


def main() -> None:
    """Entry point: run the adapter on localhost."""
    host = os.environ.get("RIPPLEBACK_ADAPTER_HOST", DEFAULT_ADAPTER_HOST)
    raw_port = os.environ.get("RIPPLEBACK_ADAPTER_PORT", str(DEFAULT_ADAPTER_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid RIPPLEBACK_ADAPTER_PORT={raw_port!r}: must be an integer.") from exc
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: binding the adapter to {host!r} exposes the MCP session "
            "beyond localhost. Use 127.0.0.1 unless you know what you are doing."
        )
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
