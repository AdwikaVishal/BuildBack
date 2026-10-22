"""Adapter REST surface via ``TestClient`` (no network, no live MCP server).

MCP-dependent routes use a hand-rolled fake client so the adapter's own
contract — auth, validation, error mapping, write gating — is what is under
test here. The real end-to-end MCP path stays covered by ``test_adapter.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient

from rippleback import adapter, repository
from rippleback.client import ConnectionState, MCPToolInfo
from rippleback.db import session_scope

SAMPLE_YAML = """\
{sid}:
  a:
    - b
"""


class FakeMCPClient:
    def __init__(
        self,
        *,
        connected: bool = True,
        tools: list[MCPToolInfo] | None = None,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self._connected = connected
        self._tools = tools or [
            MCPToolInfo(
                name="get_plan_state",
                description="Read the plan state.",
                input_schema={"type": "object", "required": ["scenario_id"]},
            )
        ]
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def health(self) -> ConnectionState:
        return ConnectionState(
            connected=self._connected,
            url="http://fake/mcp",
            server_name="RippleBack" if self._connected else None,
            server_version="9.9.9" if self._connected else None,
            tool_count=len(self._tools),
            last_error=None if self._connected else "unreachable",
        )

    async def list_tools(self, refresh: bool = False) -> list[MCPToolInfo]:
        return list(self._tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        if self._error is not None:
            raise self._error
        return self._result

    async def disconnect(self) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    # Bare client: no lifespan, so the session connection is never opened.
    return TestClient(adapter.app)


@pytest.fixture(autouse=True)
def _clear_caches():
    adapter._assistants.clear()
    yield
    adapter._assistants.clear()


def _make_scenario(sid: str) -> dict[str, Any]:
    return {
        "scenario_id": sid,
        "plan_id": f"plan-{sid}",
        "version": 1,
        "status": "healthy",
        "entities": [
            {"id": "a", "name": "A", "status": "in_progress"},
            {"id": "b", "name": "B", "status": "pending"},
        ],
        "resources": [{"id": "r1", "name": "R1", "capacity": 1}],
        "dependencies": [{"dependent": "b", "prerequisite": "a"}],
        "constraints": [],
    }


@pytest.fixture
def committed_scenario():
    sid = f"adapter-{uuid.uuid4().hex[:12]}"
    scenario = _make_scenario(sid)
    with session_scope() as session:
        repository.save_version(session, scenario, origin="test", version=1, source="test")
    return scenario


DROP = {"action": "remove_dependency", "dependent": "b", "prerequisite": "a"}


# ── status ────────────────────────────────────────────────────────────────────


class TestStatusRoute:
    def test_status_reports_config_without_secrets(self, client, monkeypatch) -> None:
        monkeypatch.setenv("JIRA_API_TOKEN", "super-secret-value")
        response = client.get("/api/status")
        assert response.status_code == 200
        body = response.json()
        assert body["adapter"]["name"] == "rippleback-adapter"
        assert "integrations" in body
        assert "demo-release-plan" in body["scenarios"]["ids"]
        assert "super-secret-value" not in response.text

    def test_unknown_route_is_404(self, client) -> None:
        assert client.get("/api/nope").status_code == 404


# ── health / tools / tool invocation ──────────────────────────────────────────


class TestMCPSurface:
    def test_health_reports_connected(self, client, monkeypatch) -> None:
        monkeypatch.setattr(adapter, "get_client", lambda: FakeMCPClient())
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["mcp"]["connected"] is True
        assert "get_plan_state" in body["tools"]

    def test_health_reports_503_when_offline(self, client, monkeypatch) -> None:
        monkeypatch.setattr(adapter, "get_client", lambda: FakeMCPClient(connected=False))
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["mcp"]["connected"] is False

    def test_tools_endpoint_lists_schemas(self, client, monkeypatch) -> None:
        monkeypatch.setattr(adapter, "get_client", lambda: FakeMCPClient())
        body = client.get("/api/mcp/tools").json()
        assert body["connected"] is True
        assert body["tools"][0]["input_schema"]["required"] == ["scenario_id"]

    def test_read_tool_call_forwards_to_the_client(self, client, monkeypatch) -> None:
        fake = FakeMCPClient(result={"scenario_id": "demo-release-plan"})
        monkeypatch.setattr(adapter, "get_client", lambda: fake)
        response = client.post(
            "/api/mcp/tool",
            json={"tool": "get_plan_state", "arguments": {"scenario_id": "demo-release-plan"}},
        )
        assert response.status_code == 200
        assert response.json()["result"] == {"scenario_id": "demo-release-plan"}
        assert fake.calls == [("get_plan_state", {"scenario_id": "demo-release-plan"})]

    def test_tool_not_found_is_404(self, client, monkeypatch) -> None:
        from rippleback.client import MCPToolNotFoundError

        fake = FakeMCPClient(error=MCPToolNotFoundError("nope"))
        monkeypatch.setattr(adapter, "get_client", lambda: fake)
        response = client.post("/api/mcp/tool", json={"tool": "nope", "arguments": {}})
        assert response.status_code == 404

    def test_tool_failure_is_reported_not_swallowed(self, client, monkeypatch) -> None:
        from rippleback.client import MCPToolFailedError

        fake = FakeMCPClient(error=MCPToolFailedError("get_plan_state", "boom"))
        monkeypatch.setattr(adapter, "get_client", lambda: fake)
        response = client.post(
            "/api/mcp/tool", json={"tool": "get_plan_state", "arguments": {}}
        )
        assert response.status_code == 200
        assert response.json()["isError"] is True
        assert "boom" in response.json()["error"]

    def test_malformed_bodies_are_400(self, client) -> None:
        assert client.post("/api/mcp/tool", content=b"not json").status_code == 400
        assert client.post("/api/mcp/tool", json={"arguments": {}}).status_code == 400
        assert (
            client.post(
                "/api/mcp/tool", json={"tool": "x", "arguments": "nope"}
            ).status_code
            == 400
        )

    def test_write_tool_is_refused_while_writes_are_disabled(
        self, client, monkeypatch
    ) -> None:
        monkeypatch.delenv("RIPPLEBACK_WRITES_ENABLED", raising=False)
        response = client.post(
            "/api/mcp/tool",
            json={"tool": "record_disruption", "arguments": {"scenario_id": "x", "entity_id": "y"}},
        )
        assert response.status_code == 403
        assert "writes are disabled" in response.json()["error"]


# ── auth middleware ───────────────────────────────────────────────────────────


class TestAuth:
    def test_mutating_route_requires_the_key(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_API_KEY", "correct-key")
        assert client.post("/api/sync").status_code == 401
        assert (
            client.post("/api/sync", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )

    def test_mutating_route_accepts_the_key(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_API_KEY", "correct-key")
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        monkeypatch.setattr(
            "rippleback.sync.sync_all", lambda: {"ok": True}
        )
        response = client.post(
            "/api/sync", headers={"Authorization": "Bearer correct-key"}
        )
        assert response.status_code == 200
        assert response.json()["report"] == {"ok": True}

    def test_get_routes_are_never_blocked(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_API_KEY", "correct-key")
        assert client.get("/api/status").status_code == 200

    def test_chat_is_auth_exempt(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_API_KEY", "correct-key")

        class FakeAssistant:
            async def handle_message(self, message, scenario_id=None):
                return _FakeReply({"reply": "hello", "steps": []})

        monkeypatch.setattr(adapter, "get_assistant", lambda name: FakeAssistant())
        response = client.post("/api/assistant/chat", json={"message": "hi"})
        assert response.status_code == 200
        assert response.json()["reply"] == "hello"

    def test_chat_requires_a_message(self, client, monkeypatch) -> None:
        assert client.post("/api/assistant/chat", json={"message": "  "}).status_code == 400


class _FakeReply:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self._payload


# ── commit flow ───────────────────────────────────────────────────────────────


class TestCommitFlow:
    def test_dry_run_is_gated_when_writes_are_disabled(
        self, client, committed_scenario
    ) -> None:
        response = client.post(
            "/api/commits/dry-run",
            json={
                "scenario_id": committed_scenario["scenario_id"],
                "expected_version": 1,
                "changes": [DROP],
            },
        )
        assert response.status_code == 403
        assert "writes are disabled" in response.json()["error"]

    def test_dry_run_then_commit_then_read_back(
        self, client, committed_scenario, monkeypatch
    ) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        sid = committed_scenario["scenario_id"]

        dry = client.post(
            "/api/commits/dry-run",
            json={"scenario_id": sid, "expected_version": 1, "changes": [DROP]},
        )
        assert dry.status_code == 200
        report = dry.json()["result"]
        assert report["feasible"] is True
        assert client.get("/api/commits", params={"scenario_id": sid}).json()["count"] == 0

        created = client.post(
            "/api/commits",
            json={
                "scenario_id": sid,
                "expected_version": 1,
                "confirm_token": report["confirm_token"],
                "changes": [DROP],
                "idempotency_key": f"adapter-{sid}",
            },
        )
        assert created.status_code == 200
        commit_id = created.json()["result"]["commit_id"]

        listing = client.get("/api/commits", params={"scenario_id": sid}).json()
        assert listing["count"] == 1
        assert listing["commits"][0]["id"] == commit_id

        detail = client.get(f"/api/commits/{commit_id}").json()
        assert detail["commit"]["changes"] == [DROP]

        audit = client.get("/api/audit", params={"scenario_id": sid}).json()
        assert audit["count"] >= 1

    def test_stale_version_maps_to_409(self, client, committed_scenario, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        response = client.post(
            "/api/commits/dry-run",
            json={
                "scenario_id": committed_scenario["scenario_id"],
                "expected_version": 99,
                "changes": [DROP],
            },
        )
        assert response.status_code == 409

    def test_unknown_scenario_maps_to_400(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        response = client.post(
            "/api/commits/dry-run",
            json={"scenario_id": "ghost", "expected_version": 1, "changes": [DROP]},
        )
        assert response.status_code == 400

    def test_bad_kind_is_400(self, client, committed_scenario, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        response = client.post(
            "/api/commits/dry-run",
            json={
                "scenario_id": committed_scenario["scenario_id"],
                "expected_version": 1,
                "changes": [DROP],
                "kind": "summon",
            },
        )
        assert response.status_code == 400

    def test_commit_requires_token_and_key(self, client, committed_scenario, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        sid = committed_scenario["scenario_id"]
        missing_token = client.post(
            "/api/commits",
            json={"scenario_id": sid, "expected_version": 1, "changes": [DROP]},
        )
        assert missing_token.status_code == 400

        missing_key = client.post(
            "/api/commits",
            json={
                "scenario_id": sid,
                "expected_version": 1,
                "changes": [DROP],
                "confirm_token": "1.abc",
            },
        )
        assert missing_key.status_code == 400

    def test_unknown_commit_is_404(self, client) -> None:
        assert client.get("/api/commits/999999").status_code == 404

    def test_retry_without_an_outbox_row_is_a_noop(self, client, committed_scenario, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        sid = committed_scenario["scenario_id"]
        monkeypatch.setattr("rippleback.integrations.outbox.kick", lambda: None)
        report = client.post(
            "/api/commits/dry-run",
            json={"scenario_id": sid, "expected_version": 1, "changes": [DROP]},
        ).json()["result"]
        commit_id = client.post(
            "/api/commits",
            json={
                "scenario_id": sid,
                "expected_version": 1,
                "confirm_token": report["confirm_token"],
                "changes": [DROP],
                "idempotency_key": f"retry-{sid}",
            },
        ).json()["result"]["commit_id"]

        response = client.post(f"/api/commits/{commit_id}/retry")
        assert response.status_code == 200
        assert response.json()["requeued"] is False

    def test_retry_unknown_commit_is_404(self, client, monkeypatch) -> None:
        monkeypatch.setattr("rippleback.integrations.outbox.kick", lambda: None)
        assert client.post("/api/commits/999999/retry").status_code == 404


# ── substitutes import ────────────────────────────────────────────────────────


class TestSubstitutesImport:
    def test_imports_a_catalog_file(self, client, committed_scenario, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        sid = committed_scenario["scenario_id"]
        path = tmp_path / "subs.yaml"
        path.write_text(SAMPLE_YAML.format(sid=sid), encoding="utf-8")
        response = client.post("/api/substitutes/import", json={"path": str(path)})
        assert response.status_code == 200
        assert response.json()["written"][sid] == 1

    def test_unknown_entity_is_rejected(self, client, committed_scenario, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        path = tmp_path / "bad.yaml"
        path.write_text(f"{committed_scenario['scenario_id']}:\n  ghost:\n    - b\n", encoding="utf-8")
        response = client.post("/api/substitutes/import", json={"path": str(path)})
        assert response.status_code in (422, 500)

    def test_missing_file_is_422(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        response = client.post(
            "/api/substitutes/import", json={"path": str(tmp_path / "absent.yaml")}
        )
        assert response.status_code == 422

    def test_path_is_required(self, client, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        assert client.post("/api/substitutes/import", json={}).status_code == 400


# ── misc contract ─────────────────────────────────────────────────────────────


class TestMiscContract:
    def test_root_hints_when_the_frontend_is_unbuilt(self, monkeypatch, tmp_path) -> None:
        # Force the "no frontend build" branch regardless of whether a local
        # frontend/dist happens to exist, then build a fresh app so the route
        # decision is made against the missing directory.
        monkeypatch.setattr(adapter, "_static_dir", lambda: tmp_path / "missing")
        response = TestClient(adapter.create_app()).get("/")
        assert response.status_code == 200
        assert "hint" in response.json()

    def test_commits_limit_must_be_an_integer(self, client) -> None:
        assert client.get("/api/commits", params={"limit": "lots"}).status_code == 400
        assert client.get("/api/audit", params={"limit": "lots"}).status_code == 400
