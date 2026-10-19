"""Genuine MCP client for the RippleBack server (official Python MCP SDK).

This module is the single place that speaks MCP to the RippleBack server at
``RIPPLEBACK_MCP_URL`` (default ``http://127.0.0.1:8000/mcp``) over the
Streamable HTTP transport, using the official SDK client
(``mcp.ClientSession`` + ``mcp.client.streamable_http.streamable_http_client``).

Responsibilities:

* **Connection management** — one initialized MCP session is reused across
  requests; ``connect``/``disconnect`` manage the lifecycle; transport failures
  mark the client disconnected and the next call reconnects cleanly. Sessions
  are never leaked: everything lives on an ``AsyncExitStack`` that is closed on
  disconnect.
* **Tool discovery** — ``list_tools`` calls the live server's ``tools/list``
  and caches each tool's name, description, and JSON input schema. Nothing is
  hardcoded, so newly registered tools (e.g. a scheduling tool from Person 3)
  are discovered automatically.
* **Tool invocation** — ``call_tool`` validates arguments against the
  discovered input schema (required fields, unknown fields, scalar types), then
  performs a real MCP ``tools/call``. MCP tool errors (``isError``) are raised
  as :class:`MCPToolFailedError` — they are never converted into
  successful-looking responses.
* **Concurrency** — session access is serialized with an ``asyncio.Lock`` so
  concurrent requests cannot corrupt MCP session state.
* **Narrow retries** — safe read operations (``list_tools``, health checks)
  reconnect and retry once after a transport failure. Tool calls are **never**
  silently retried, because future tools may not be read-only; a failed call
  surfaces a clear, recoverable error instead.

All current RippleBack tools are read-only, but this client does not assume
that of future tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _externally_cancelled() -> bool:
    """True when the *current task* has a pending external cancellation.

    The SDK's Streamable HTTP transport surfaces transport-level failures
    (e.g. connection refused) to the caller as ``CancelledError`` from its
    internal anyio task group. That must become an ``MCPConnectionError``.
    A genuine cancellation of our own task (e.g. server shutdown) must
    propagate unchanged, which this check distinguishes via
    ``Task.cancelling()`` (Python 3.11+).
    """
    task = asyncio.current_task()
    if task is None:
        return False
    try:
        return bool(task.cancelling())
    except AttributeError:  # pragma: no cover - Python < 3.11
        return False


class MCPClientError(Exception):
    """Base error for the RippleBack MCP client."""


class MCPConnectionError(MCPClientError):
    """The MCP server could not be reached or the session failed."""


class MCPToolNotFoundError(MCPClientError):
    """The requested tool is not in the live-discovered tool list."""


class MCPToolFailedError(MCPClientError):
    """The MCP server executed the tool but returned an error result."""

    def __init__(self, tool: str, message: str):
        super().__init__(message)
        self.tool = tool
        self.message = message


@dataclass(frozen=True)
class MCPToolInfo:
    """One discovered MCP tool: name, description, and input schema."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolCallRecord:
    """Audit record of one tool invocation (no payloads beyond arguments)."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    ts: float
    error: str | None = None


@dataclass
class ConnectionState:
    """Snapshot of the client's connection state for health endpoints."""

    connected: bool
    url: str
    server_name: str | None = None
    server_version: str | None = None
    tool_count: int = 0
    last_error: str | None = None
    checked_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "url": self.url,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "tool_count": self.tool_count,
            "last_error": self.last_error,
            "checked_at": self.checked_at,
        }


class RippleBackMCPClient:
    """Async MCP client bound to one RippleBack server endpoint.

    Use as an async context manager, or call :meth:`connect` /
    :meth:`disconnect` explicitly::

        client = RippleBackMCPClient()
        await client.connect()
        tools = await client.list_tools()
        plan = await client.call_tool("get_plan_state", {"scenario_id": "travel"})
        await client.disconnect()
    """

    def __init__(
        self,
        url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.url = url or os.environ.get("RIPPLEBACK_MCP_URL", DEFAULT_MCP_URL)
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: dict[str, MCPToolInfo] = {}
        self._server_name: str | None = None
        self._server_version: str | None = None
        self._last_error: str | None = None
        self.history: list[ToolCallRecord] = []

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def __aenter__(self) -> "RippleBackMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self) -> ConnectionState:
        """Open and initialize an MCP session. Idempotent when connected."""
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> ConnectionState:
        """Connect while the caller already holds ``self._lock``."""
        if self._session is not None:
            return self._state()
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            streams = await stack.enter_async_context(
                streamable_http_client(self.url)
            )
            read, write = streams
            session = await stack.enter_async_context(
                ClientSession(read, write)
            )
            init = await asyncio.wait_for(
                session.initialize(), timeout=self.timeout
            )
        except asyncio.CancelledError as exc:
            # The SDK's transport task group surfaces connection failures
            # (e.g. connection refused) as CancelledError. Distinguish that
            # from a genuine external cancellation of *our* task.
            await self._close_stack_quietly(stack)
            if _externally_cancelled():
                raise
            self._last_error = "MCP connection failed during initialize"
            raise MCPConnectionError(self._last_error) from exc
        except asyncio.TimeoutError as exc:
            await self._close_stack_quietly(stack)
            self._last_error = (
                f"MCP initialize timed out after {self.timeout}s"
            )
            raise MCPConnectionError(self._last_error) from exc
        except Exception as exc:  # transport / handshake failure
            await self._close_stack_quietly(stack)
            self._last_error = f"MCP connection failed: {exc}"
            raise MCPConnectionError(self._last_error) from exc

        self._stack = stack
        self._session = session
        # MCP v2 SDK models use snake_case (server_info, not serverInfo).
        server_info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
        self._server_name = getattr(server_info, "name", None)
        self._server_version = getattr(server_info, "version", None)
        self._last_error = None
        try:
            await self._refresh_tools_locked()
        except (Exception, asyncio.CancelledError) as exc:
            self._last_error = f"tool discovery failed: {exc}"
        return self._state()

    async def disconnect(self) -> None:
        """Close the session and release every transport resource."""
        async with self._lock:
            await self._teardown_locked("disconnected")

    async def _teardown_locked(self, reason: str) -> None:
        """Drop the session and close its transport in the *current* task.

        The exit stack owns anyio task groups from the SDK transport, which
        must be closed in the same task that entered them — never in a
        fire-and-forget task. Callers must already hold ``self._lock``.
        """
        self._last_error = reason
        self._session = None
        self._tools = {}
        stack, self._stack = self._stack, None
        await self._close_stack_quietly(stack)

    @staticmethod
    async def _close_stack_quietly(stack: AsyncExitStack | None) -> None:
        """Close an exit stack, swallowing any close-time error.

        Closing a dead transport must never mask the error being handled.
        """
        if stack is None:
            return
        try:
            await stack.aclose()
        except BaseException:
            pass

    def _state(self) -> ConnectionState:
        return ConnectionState(
            connected=self._session is not None,
            url=self.url,
            server_name=self._server_name,
            server_version=self._server_version,
            tool_count=len(self._tools),
            last_error=self._last_error,
            checked_at=time.time(),
        )

    async def health(self) -> ConnectionState:
        """Cheap liveness probe: reconnect if needed and report state."""
        if self._session is None:
            try:
                await self.connect()
            except MCPConnectionError:
                return self._state()
        return self._state()

    async def _ensure_connected(self) -> ClientSession:
        """Ensure a live session; callers must already hold ``self._lock``."""
        if self._session is None:
            await self._connect_locked()
        assert self._session is not None
        return self._session

    # ── Tool discovery ───────────────────────────────────────────────────────

    async def _refresh_tools_locked(self) -> list[MCPToolInfo]:
        session = await self._ensure_connected()
        result = await asyncio.wait_for(
            session.list_tools(), timeout=self.timeout
        )
        discovered: dict[str, MCPToolInfo] = {}
        for tool in result.tools:
            schema = getattr(tool, "input_schema", None) or {}
            discovered[tool.name] = MCPToolInfo(
                name=tool.name,
                description=(tool.description or "").strip(),
                input_schema=schema if isinstance(schema, dict) else {},
            )
        self._tools = discovered
        return list(self._tools.values())

    async def list_tools(self, refresh: bool = False) -> list[MCPToolInfo]:
        """Return the live-discovered tool list (cached until refresh).

        Safe to retry on transport failure: ``tools/list`` is a read-only MCP
        operation.
        """
        async with self._lock:
            if self._session is not None and self._tools and not refresh:
                return list(self._tools.values())
            try:
                return await self._refresh_tools_locked()
            except MCPConnectionError:
                raise
            except asyncio.CancelledError as exc:
                if _externally_cancelled():
                    raise
                await self._teardown_locked("tool discovery transport failure")
                raise MCPConnectionError("tool discovery transport failure") from exc
            except Exception as exc:
                await self._teardown_locked(f"tool discovery failed: {exc}")
                raise MCPConnectionError(
                    f"tool discovery failed: {exc}"
                ) from exc

    def get_tool(self, name: str) -> MCPToolInfo | None:
        return self._tools.get(name)

    # ── Argument validation ──────────────────────────────────────────────────

    async def validate_arguments(
        self, tool: str, arguments: dict[str, Any]
    ) -> list[str]:
        """Validate arguments against the discovered input schema.

        Returns a list of human-readable problems (empty when valid). This is
        intentionally shallow — required fields, unknown fields, and top-level
        scalar types — because the MCP server remains the authority.
        """
        tools = await self.list_tools()
        info = next((t for t in tools if t.name == tool), None)
        if info is None:
            return [f"unknown tool {tool!r}"]
        schema = info.input_schema or {}
        problems: list[str] = []

        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in arguments or arguments[key] is None:
                problems.append(f"missing required argument {key!r}")

        additional = schema.get("additionalProperties", True)
        if additional is False and properties:
            for key in arguments:
                if key not in properties:
                    problems.append(f"unknown argument {key!r}")

        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, value in arguments.items():
            declared = properties.get(key) if isinstance(properties, dict) else None
            if not isinstance(declared, dict):
                continue
            expected = declared.get("type")
            py_type = type_map.get(expected) if isinstance(expected, str) else None
            if py_type is None:
                continue
            if isinstance(value, bool) and py_type in (int, (int, float)):
                problems.append(f"argument {key!r} must be {expected}, got boolean")
                continue
            if not isinstance(value, py_type):
                problems.append(
                    f"argument {key!r} must be {expected}, got {type(value).__name__}"
                )
        return problems

    # ── Tool invocation ──────────────────────────────────────────────────────

    async def call_tool(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke one MCP tool and return its parsed structured result.

        Raises:
            MCPToolNotFoundError: the tool is not in the discovered list.
            MCPConnectionError: the server could not be reached / session died.
            MCPToolFailedError: the server ran the tool and returned an error.
        """
        args = dict(arguments or {})
        tools = await self.list_tools()
        if tool not in {t.name for t in tools}:
            raise MCPToolNotFoundError(
                f"unknown tool {tool!r}; available: {sorted(t.name for t in tools)}"
            )

        problems = await self.validate_arguments(tool, args)
        if problems:
            raise MCPToolFailedError(tool, "invalid arguments: " + "; ".join(problems))

        started = time.monotonic()
        async with self._lock:
            session = await self._ensure_connected()
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool, args), timeout=self.timeout
                )
            except asyncio.CancelledError as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                if _externally_cancelled():
                    raise
                self._history(tool, args, False, duration_ms, "transport failure")
                await self._teardown_locked(f"transport failure during {tool!r}")
                raise MCPConnectionError(
                    f"transport failure during {tool!r}; "
                    "the connection will be re-established on the next request"
                ) from exc
            except asyncio.TimeoutError as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                self._history(tool, args, False, duration_ms, f"timed out after {self.timeout}s")
                await self._teardown_locked(f"tool call {tool!r} timed out")
                raise MCPConnectionError(
                    f"tool call {tool!r} timed out after {self.timeout}s; "
                    "the connection will be re-established on the next request"
                ) from exc
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                self._history(tool, args, False, duration_ms, str(exc))
                await self._teardown_locked(f"transport failure during {tool!r}: {exc}")
                raise MCPConnectionError(
                    f"transport failure during {tool!r}: {exc}; "
                    "the connection will be re-established on the next request"
                ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        if getattr(result, "is_error", False):
            text = ""
            content = getattr(result, "content", None) or []
            if content:
                text = getattr(content[0], "text", "") or ""
            message = text or f"tool {tool!r} returned an error"
            self._history(tool, args, False, duration_ms, message)
            raise MCPToolFailedError(tool, message)

        parsed = self._parse_result(result)
        self._history(tool, args, True, duration_ms, None)
        return parsed

    @staticmethod
    def _parse_result(result: Any) -> Any:
        """Parse MCP tool content (JSON text) into Python structures."""
        content = getattr(result, "content", None) or []
        if not content:
            return None
        first = content[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return text
        return first if isinstance(first, dict) else str(first)

    def _history(
        self,
        tool: str,
        args: dict[str, Any],
        ok: bool,
        duration_ms: int,
        error: str | None,
    ) -> None:
        self.history.append(
            ToolCallRecord(
                tool=tool,
                arguments=args,
                ok=ok,
                duration_ms=duration_ms,
                ts=time.time(),
                error=error,
            )
        )
        if len(self.history) > 500:
            del self.history[:-250]
