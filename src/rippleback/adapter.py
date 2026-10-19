"""Local API adapter between the React frontend and the RippleBack MCP server.

Why this exists: browsers cannot speak the MCP Streamable HTTP protocol
directly (SSE framing, session headers, official-SDK handshake), and MCP
sessions must not be exposed to the browser anyway. This adapter is the
**single genuine MCP client** of the application: it owns one
:class:`rippleback.client.RippleBackMCPClient` (official Python MCP SDK)
and exposes a narrow, validated REST surface to the dashboard:

* ``GET  /api/health``        — adapter + MCP connection status, discovered tool names.
* ``GET  /api/status``        — configuration/integration report (names only, never values).
* ``GET  /api/mcp/tools``     — live-discovered tools with descriptions + input schemas.
* ``POST /api/mcp/tool``      — ``{tool, arguments}`` → structured result or a clear error.
* ``POST /api/assistant/chat``— ``{message, scenario_id?}`` → assistant reply + tool trace.
* ``POST /api/sync``          — run one PM sync + PagerDuty ingest pass now.
* ``GET  /api/commits``       — recent committed recoveries (with diff + push state).
* ``POST /api/commits/dry-run`` — diff + confirm token; never mutates.
* ``POST /api/commits``       — commit a confirmed change set (write-gated).
* ``POST /api/commits/{id}/retry`` — re-queue a failed PM write-back push.
* ``GET  /api/audit``         — recent audit rows (already redacted at write time).
* ``POST /api/substitutes/import`` — import a substitute catalog file.

Security model:

* Mutating (non-GET) routes require ``Authorization: Bearer <RIPPLEBACK_API_KEY>``
  whenever ``RIPPLEBACK_API_KEY`` is configured. Chat and ``/api/mcp/tool`` are
  auth-exempt; the latter applies the shared :func:`write_guard_reason` policy to
  write tools only, so read tools stay usable without a key.
* Plan-state changes additionally require ``RIPPLEBACK_WRITES_ENABLED=true``.
* A non-loopback bind with no API key refuses writes outright.

It binds to localhost only (override with ``RIPPLEBACK_ADAPTER_HOST``) and
keeps every credential (including AWS for the optional Bedrock provider) on
the backend. Business logic stays in the MCP server's engines; this adapter
only forwards, validates, and explains. When ``frontend/dist`` exists it is
served as static files, so one process can host the whole dashboard.

Start it with ``rippleback-adapter`` (or ``python -m rippleback.adapter``)
after starting the MCP server.
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from rippleback import repository, seeder
from rippleback.assistant.orchestrator import Assistant
from rippleback.assistant.providers import make_provider
from rippleback.client import (
    MCPConnectionError,
    MCPToolFailedError,
    MCPToolNotFoundError,
    RippleBackMCPClient,
)
from rippleback.commit import (
    CommitError,
    commit_reassignment,
    commit_recovery,
    dry_run_commit,
)
from rippleback.config import integration_status, load_settings
from rippleback.evaluation import EvaluationError
from rippleback.security import (
    check_api_key,
    extract_bearer,
    redact,
    write_guard_reason,
)
from rippleback.scoring import ScoringError

DEFAULT_ADAPTER_HOST = "127.0.0.1"
DEFAULT_ADAPTER_PORT = 8001

ADAPTER_NAME = "rippleback-adapter"
ADAPTER_VERSION = "0.1.0"

# Tools that mutate plan state; they pass through write_guard_reason.
WRITE_TOOL_NAMES = frozenset(
    {
        "dry_run_recovery_plan",
        "record_disruption",
        "resolve_disruption",
        "commit_recovery_plan",
        "commit_reassignment",
    }
)

# POST routes that do NOT require the API key (they have their own policy).
_AUTH_EXEMPT = frozenset({"/api/assistant/chat", "/api/mcp/tool"})

_client: RippleBackMCPClient | None = None
_assistants: dict[str, Assistant] = {}
_SYNC_STOP: threading.Event | None = None


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


def _json_error(message: str, *, status_code: int, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"error": redact(message), "isError": True, **extra}, status_code=status_code
    )


def _bad_body(message: str, status_code: int = 400) -> JSONResponse:
    return _json_error(message, status_code=status_code)


def _commit_error_status(exc: CommitError) -> int:
    text = str(exc)
    if text.startswith("writes are disabled"):
        return 403
    if "stale" in text:
        return 409
    return 400


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


async def status(request: Request) -> JSONResponse:
    """Configuration report: integration names, writes state, scenario counts.

    Never includes secret values — :func:`rippleback.config.integration_status`
    reports variable names only.
    """
    try:
        scenarios = repository.list_scenarios()
    except Exception as exc:  # noqa: BLE001 - status must not 500
        return _json_error(str(exc), status_code=500)
    return JSONResponse(
        {
            "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
            "integrations": integration_status(),
            "scenarios": {
                "count": scenarios["count"],
                "ids": [entry["scenario_id"] for entry in scenarios["scenarios"]],
                "items": scenarios["scenarios"],
            },
        }
    )


async def list_tools(request: Request) -> JSONResponse:
    client = get_client()
    try:
        tools = await client.list_tools(refresh="refresh" in request.query_params)
    except MCPConnectionError as exc:
        return _json_error(str(exc), status_code=503, tools=[])
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
        return _bad_body("request body must be JSON")
    if not isinstance(body, dict):
        return _bad_body("request body must be a JSON object")

    tool = body.get("tool")
    arguments = body.get("arguments", {})
    if not isinstance(tool, str) or not tool:
        return _bad_body('missing "tool" field')
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _bad_body('"arguments" must be an object')

    if tool in WRITE_TOOL_NAMES:
        reason = write_guard_reason(tool, is_write=True, headers=request.headers)
        if reason is not None:
            return _json_error(reason, status_code=403, tool=tool)

    client = get_client()
    try:
        result = await client.call_tool(tool, arguments)
    except MCPToolNotFoundError as exc:
        return _json_error(str(exc), status_code=404, tool=tool)
    except MCPToolFailedError as exc:
        # A genuine MCP tool error: reported as an error, never as success.
        return _json_error(exc.message, status_code=200, tool=tool)
    except MCPConnectionError as exc:
        return _json_error(str(exc), status_code=503, tool=tool)
    return JSONResponse({"result": result, "isError": False})


async def chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _bad_body("request body must be JSON")
    if not isinstance(body, dict):
        return _bad_body("request body must be a JSON object")

    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return _bad_body('"message" must be a non-empty string')
    scenario_id = body.get("scenario_id")
    if scenario_id is not None and not isinstance(scenario_id, str):
        return _bad_body('"scenario_id" must be a string')
    provider_name = body.get("provider")
    if provider_name is not None and not isinstance(provider_name, str):
        return _bad_body('"provider" must be a string')

    assistant = get_assistant(provider_name)
    response = await assistant.handle_message(message.strip(), scenario_id=scenario_id)
    return JSONResponse(response.to_dict())


async def sync_now(request: Request) -> JSONResponse:
    """Run one PM sync + PagerDuty ingest pass (blocking; offloaded to a thread)."""
    from rippleback import sync as sync_module

    try:
        report = await asyncio.to_thread(sync_module.sync_all)
    except Exception as exc:  # noqa: BLE001 - surfaced as a structured error
        return _json_error(str(exc), status_code=502)
    return JSONResponse({"ok": True, "report": report})


async def import_substitutes(request: Request) -> JSONResponse:
    """Import a substitute catalog from a YAML file (path relative to cwd)."""
    try:
        body = await request.json()
    except Exception:
        return _bad_body("request body must be JSON")
    if not isinstance(body, dict):
        return _bad_body("request body must be a JSON object")
    path = body.get("path")
    if not isinstance(path, str) or not path.strip():
        return _bad_body('"path" must be a non-empty string')
    try:
        counts = seeder.import_substitute_file(path.strip())
    except seeder.SeedError as exc:
        return _json_error(str(exc), status_code=422)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), status_code=500)
    return JSONResponse({"ok": True, "written": counts})


async def list_commits(request: Request) -> JSONResponse:
    scenario_id = request.query_params.get("scenario_id")
    limit_raw = request.query_params.get("limit", "50")
    try:
        limit = int(limit_raw)
    except ValueError:
        return _bad_body('"limit" must be an integer')
    rows = repository.list_commits(scenario_id=scenario_id, limit=limit)
    return JSONResponse({"count": len(rows), "commits": rows})


async def dry_run(request: Request) -> JSONResponse:
    """Diff a proposed change set and issue a confirm token — no mutation."""
    try:
        body = await request.json()
    except Exception:
        return _bad_body("request body must be JSON")
    if not isinstance(body, dict):
        return _bad_body("request body must be a JSON object")
    scenario_id = body.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        return _bad_body('"scenario_id" must be a non-empty string')
    kind = body.get("kind", "recovery")
    if kind not in ("recovery", "reassignment"):
        return _bad_body('"kind" must be "recovery" or "reassignment"')
    try:
        report = dry_run_commit(
            scenario_id,
            expected_version=body.get("expected_version"),
            changes=body.get("changes"),
            option_id=body.get("option_id"),
            kind=kind,
        )
    except CommitError as exc:
        return _json_error(str(exc), status_code=_commit_error_status(exc))
    except (EvaluationError, ScoringError) as exc:
        return _json_error(str(exc), status_code=422)
    except Exception as exc:  # noqa: BLE001 - ScenarioError and friends
        return _json_error(str(exc), status_code=400)
    return JSONResponse({"result": report, "isError": False})


async def create_commit(request: Request) -> JSONResponse:
    """Commit a confirmed recovery or reassignment (write-gated, idempotent)."""
    try:
        body = await request.json()
    except Exception:
        return _bad_body("request body must be JSON")
    if not isinstance(body, dict):
        return _bad_body("request body must be a JSON object")
    scenario_id = body.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        return _bad_body('"scenario_id" must be a non-empty string')
    confirm_token = body.get("confirm_token")
    if not isinstance(confirm_token, str) or not confirm_token:
        return _bad_body('"confirm_token" must be a non-empty string')
    idempotency_key = body.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        return _bad_body('"idempotency_key" must be a non-empty string')
    actor = body.get("actor") if isinstance(body.get("actor"), str) else None
    changes = body.get("changes")
    option_id = body.get("option_id") if isinstance(body.get("option_id"), str) else None
    kind = body.get("kind", "recovery")
    if kind not in ("recovery", "reassignment"):
        return _bad_body('"kind" must be "recovery" or "reassignment"')
    try:
        if kind == "reassignment":
            if not isinstance(changes, list):
                return _bad_body('"changes" must be a list for reassignment')
            result = commit_reassignment(
                scenario_id,
                expected_version=body.get("expected_version"),
                confirm_token=confirm_token,
                changes=changes,
                idempotency_key=idempotency_key,
                actor=actor,
            )
        else:
            result = commit_recovery(
                scenario_id,
                expected_version=body.get("expected_version"),
                confirm_token=confirm_token,
                changes=changes,
                option_id=option_id,
                idempotency_key=idempotency_key,
                actor=actor,
            )
    except CommitError as exc:
        return _json_error(str(exc), status_code=_commit_error_status(exc))
    except (EvaluationError, ScoringError) as exc:
        return _json_error(str(exc), status_code=422)
    except Exception as exc:  # noqa: BLE001 - ScenarioError and constraint errors
        return _json_error(str(exc), status_code=400)
    return JSONResponse({"result": result, "isError": False})


async def get_commit(request: Request) -> JSONResponse:
    commit_id_raw = request.path_params.get("commit_id", "")
    try:
        commit_id = int(commit_id_raw)
    except (TypeError, ValueError):
        return _bad_body("commit id must be an integer")
    row = repository.get_commit(commit_id)
    if row is None:
        return _json_error(f"unknown commit id {commit_id}", status_code=404)
    return JSONResponse({"commit": row})


async def retry_commit(request: Request) -> JSONResponse:
    """Re-queue a failed PM write-back push and kick the outbox worker."""
    from rippleback.integrations import outbox as outbox_module

    commit_id_raw = request.path_params.get("commit_id", "")
    try:
        commit_id = int(commit_id_raw)
    except (TypeError, ValueError):
        return _bad_body("commit id must be an integer")
    try:
        result = outbox_module.retry_commit(commit_id)
    except Exception as exc:  # noqa: BLE001
        return _json_error(str(exc), status_code=500)
    if result.get("status") == "unknown":
        return _json_error(f"unknown commit id {commit_id}", status_code=404)
    return JSONResponse({"ok": True, **result})


async def list_audit(request: Request) -> JSONResponse:
    scenario_id = request.query_params.get("scenario_id")
    limit_raw = request.query_params.get("limit", "50")
    try:
        limit = int(limit_raw)
    except ValueError:
        return _bad_body('"limit" must be an integer')
    rows = repository.list_audit(scenario_id=scenario_id, limit=limit)
    return JSONResponse({"count": len(rows), "audit": rows})


# ── Auth middleware ───────────────────────────────────────────────────────────


class ApiKeyMiddleware:
    """Require the configured bearer key on every mutating (non-GET) route.

    Pure ASGI on purpose: Starlette's ``BaseHTTPMiddleware`` wraps downstream
    requests in its own anyio task, which corrupts cancel scopes used by the
    MCP SDK client. Exempt paths use their own policy (chat is read-only;
    ``/api/mcp/tool`` applies :func:`write_guard_reason` per tool). With no API
    key configured nothing is blocked here — write gating still applies where
    it matters.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method") in ("GET", "HEAD", "OPTIONS") or scope.get(
            "path"
        ) in _AUTH_EXEMPT:
            await self.app(scope, receive, send)
            return
        settings = load_settings()
        provided = extract_bearer(Headers(scope=scope))
        if settings.api_key_required and not check_api_key(provided):
            response = JSONResponse(
                {
                    "error": redact(
                        "invalid or missing API key "
                        "(Authorization: Bearer <RIPPLEBACK_API_KEY>)"
                    ),
                    "isError": True,
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _cors_origins() -> list[str]:
    return list(load_settings().cors_origins)


def _static_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "frontend" / "dist"


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: Starlette):
    global _SYNC_STOP
    # Lazy MCP connection (established on first use) so the dashboard and the
    # adapter can start before the MCP server is up. The database is created
    # eagerly so the status/commits/audit routes work on a fresh checkout.
    try:
        from rippleback.db import init_db

        init_db()
    except Exception as exc:  # noqa: BLE001 - fail loudly, never leak secrets
        raise RuntimeError(redact(str(exc))) from exc

    settings = load_settings()
    stop: threading.Event | None = None
    if settings.pm_provider != "none" and settings.sync_interval_seconds > 0:
        from rippleback import sync as sync_module

        stop = threading.Event()
        _SYNC_STOP = stop
        sync_module.start_sync_loop(stop)
    try:
        yield
    finally:
        if stop is not None:
            stop.set()
        await get_client().disconnect()


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> Starlette:
    """Build the Starlette app (routes, auth middleware, CORS, static files)."""
    routes: list[Any] = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/mcp/tools", list_tools, methods=["GET"]),
        Route("/api/mcp/tool", call_tool, methods=["POST"]),
        Route("/api/assistant/chat", chat, methods=["POST"]),
        Route("/api/sync", sync_now, methods=["POST"]),
        Route("/api/substitutes/import", import_substitutes, methods=["POST"]),
        Route("/api/commits", list_commits, methods=["GET"]),
        Route("/api/commits", create_commit, methods=["POST"]),
        Route("/api/commits/dry-run", dry_run, methods=["POST"]),
        Route("/api/commits/{commit_id:int}/retry", retry_commit, methods=["POST"]),
        Route("/api/commits/{commit_id:int}", get_commit, methods=["GET"]),
        Route("/api/audit", list_audit, methods=["GET"]),
    ]
    dist = _static_dir()
    if dist.is_dir():
        routes.append(Mount("/", StaticFiles(directory=str(dist), html=True), name="static"))
    else:
        async def index(_request: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "name": ADAPTER_NAME,
                    "version": ADAPTER_VERSION,
                    "hint": "frontend not built; run 'npm run build' in frontend/ "
                    "to serve the dashboard from this adapter",
                }
            )

        routes.append(Route("/", index, methods=["GET"]))

    return Starlette(
        routes=routes,
        middleware=[
            Middleware(ApiKeyMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=_cors_origins(),
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
            ),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main() -> None:
    """Entry point: run the adapter on localhost."""
    host = os.environ.get("RIPPLEBACK_ADAPTER_HOST", DEFAULT_ADAPTER_HOST)
    raw_port = os.environ.get("RIPPLEBACK_ADAPTER_PORT", str(DEFAULT_ADAPTER_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid RIPPLEBACK_ADAPTER_PORT={raw_port!r}: must be an integer.") from exc
    if host not in ("127.0.0.1", "localhost", "::1"):
        settings = load_settings()
        print(
            f"WARNING: binding the adapter to {host!r} exposes the MCP session "
            "beyond localhost. Use 127.0.0.1 unless you know what you are doing."
        )
        if not settings.api_key_required:
            print(
                "WARNING: RIPPLEBACK_API_KEY is not set; all writes will be "
                "refused while bound to a non-loopback host."
            )
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
