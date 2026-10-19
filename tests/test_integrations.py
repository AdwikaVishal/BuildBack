"""PM integrations: Jira fetch/push, PagerDuty ingest, outbox worker.

All HTTP goes through ``httpx.MockTransport`` — no network, deterministic.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from rippleback import repository
from rippleback.integrations import IntegrationError, build_scenario, get_provider
from rippleback.integrations.base import (
    ImportedDependency,
    ImportedEntity,
    ImportedPlan,
    ImportedResource,
    map_status,
    parse_status_map,
    sanitize_id,
)
from rippleback.integrations.outbox import MAX_ATTEMPTS, process_outbox, retry_commit
from rippleback.integrations.pagerduty import (
    PagerDutyProvider,
    parse_service_map,
)
from rippleback.db import session_scope

JIRA_ENV = {
    "JIRA_BASE_URL": "https://acme.atlassian.net",
    "JIRA_EMAIL": "bot@acme.test",
    "JIRA_API_TOKEN": "jira-secret-token",
    "JIRA_PROJECT_KEY": "PAY",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net"
    )


def _incidents_for(scenario_id: str):
    from sqlalchemy import select

    from rippleback.db import IncidentHistory, session_scope

    with session_scope() as session:
        return list(
            session.execute(
                select(IncidentHistory).where(
                    IncidentHistory.scenario_id == scenario_id
                )
            ).scalars().all()
        )


@pytest.fixture
def jira_env(monkeypatch: pytest.MonkeyPatch):
    for key, value in JIRA_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("JIRA_STATUS_MAP", raising=False)
    monkeypatch.delenv("RIPPLEBACK_OUT_OF_SCOPE_LINKS", raising=False)


# ── factory ───────────────────────────────────────────────────────────────────


class TestProviderFactory:
    def test_no_provider_configured_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "none")
        with pytest.raises(IntegrationError, match="no PM provider") as excinfo:
            get_provider()
        assert excinfo.value.missing == ["RIPPLEBACK_PM_PROVIDER"]

    def test_unknown_provider_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "trello")
        with pytest.raises(IntegrationError, match="unknown RIPPLEBACK_PM_PROVIDER"):
            get_provider("trello")

    def test_missing_credentials_are_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "jira")
        for name in JIRA_ENV:
            monkeypatch.delenv(name, raising=False)
        provider = get_provider("jira")
        with pytest.raises(IntegrationError, match="missing:") as excinfo:
            provider.require_configured()
        assert set(excinfo.value.missing) == set(JIRA_ENV)

    def test_explicit_name_overrides_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "none")
        for key, value in JIRA_ENV.items():
            monkeypatch.setenv(key, value)
        assert get_provider("jira").name == "jira"

    def test_injected_client_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in JIRA_ENV.items():
            monkeypatch.setenv(key, value)
        client = _client(lambda request: httpx.Response(200, json={}))
        provider = get_provider("jira", client=client)
        assert provider._client is client
        assert provider._owns_client is False


# ── normalization helpers ─────────────────────────────────────────────────────


class TestStatusMapping:
    def test_explicit_override_beats_the_default_table(self) -> None:
        assert map_status("In Review", None, {"in review": "in_progress"}) == "in_progress"

    def test_default_table_maps_common_statuses(self) -> None:
        assert map_status("Done", None) == "done"
        assert map_status(None, "indeterminate") == "in_progress"
        assert map_status("Weird", None) == "pending"

    def test_invalid_status_map_entries_are_rejected(self) -> None:
        with pytest.raises(IntegrationError, match="invalid status mapping"):
            parse_status_map("Blocked:not_a_status")

    def test_sanitize_id_is_stable(self) -> None:
        assert sanitize_id("My Project!") == "my-project"
        assert sanitize_id("") == "plan"


class TestBuildScenario:
    def test_entities_resources_and_release_constraint(self) -> None:
        plan = ImportedPlan(
            provider="jira",
            scenario_id="jira:pay",
            plan_id="jira-pay",
            external_id="PAY",
            entities=[
                ImportedEntity(
                    entity_id="PAY-1",
                    name="Payment API",
                    status="in_progress",
                    external_id="10001",
                    assignee="acc-1",
                    effort=3,
                    cost=500.0,
                ),
                ImportedEntity(
                    entity_id="PAY-2",
                    name="Release",
                    status="pending",
                    assignee="acc-1",
                ),
            ],
            dependencies=[ImportedDependency("PAY-2", "PAY-1")],
            release_entity="PAY-2",
            resources=[ImportedResource("acc-1", "Alice", 2)],
        )
        scenario = build_scenario(plan)
        assert scenario["scenario_id"] == "jira:pay"
        assert scenario["version"] == 1
        first = scenario["entities"][0]
        assert first["effort"] == 3
        assert first["metadata"]["cost"] == 500.0
        assert first["metadata"]["external_id"] == "10001"
        assert scenario["dependencies"] == [{"dependent": "PAY-2", "prerequisite": "PAY-1"}]
        assert scenario["constraints"][0]["type"] == "all_entities_done_before"
        assert scenario["resources"][0]["id"] == "acc-1"

    def test_release_entity_must_be_imported(self) -> None:
        plan = ImportedPlan(
            provider="jira",
            scenario_id="jira:pay",
            plan_id="jira-pay",
            external_id="PAY",
            entities=[ImportedEntity("PAY-1", "One")],
            dependencies=[],
            release_entity="PAY-9",
        )
        with pytest.raises(IntegrationError, match="RIPPLEBACK_RELEASE_ENTITY"):
            build_scenario(plan)

    def test_scenario_passes_validation(self) -> None:
        from rippleback.scenarios import validate_scenario

        plan = ImportedPlan(
            provider="jira",
            scenario_id="jira:pay",
            plan_id="jira-pay",
            external_id="PAY",
            entities=[ImportedEntity("PAY-1", "One", assignee="acc-1")],
            dependencies=[],
        )
        assert validate_scenario(build_scenario(plan)) == []


# ── Jira ──────────────────────────────────────────────────────────────────────


def _jira_search_payload(issues: list[dict[str, Any]], next_token: str | None = None):
    payload: dict[str, Any] = {"issues": issues, "isLast": next_token is None}
    if next_token:
        payload["nextPageToken"] = next_token
    return payload


def _issue(key: str, summary: str, **fields: Any) -> dict[str, Any]:
    base = {
        "summary": summary,
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "assignee": {"accountId": "acc-1", "displayName": "Alice"},
        "issuelinks": [],
        "labels": [],
    }
    base.update(fields)
    return {"key": key, "id": key.replace("PAY-", "1000"), "fields": base}


class TestJiraFetch:
    def test_fetches_entities_and_dependencies(self, jira_env) -> None:
        issues = [
            _issue(
                "PAY-1",
                "Payment API",
                issuelinks=[
                    {
                        "type": {"name": "Blocks"},
                        "outwardIssue": {"key": "PAY-2"},
                    }
                ],
            ),
            _issue("PAY-2", "Release", status={"name": "Done", "statusCategory": {"key": "done"}}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/rest/api/3/search/jql"
            body = json.loads(request.content)
            assert body["fields"]  # at least the default fields
            return httpx.Response(200, json=_jira_search_payload(issues))

        provider = get_provider("jira", client=_client(handler))
        plan = provider.fetch_plan()
        assert plan.scenario_id == "jira:pay"
        statuses = {entity.entity_id: entity.status for entity in plan.entities}
        assert statuses == {"PAY-1": "in_progress", "PAY-2": "done"}
        assert {(d.dependent, d.prerequisite) for d in plan.dependencies} == {("PAY-1", "PAY-2")}

    def test_blocked_label_marks_blocked_unless_done(self, jira_env) -> None:
        issues = [
            _issue("PAY-1", "A", labels=["blocked"]),
            _issue(
                "PAY-2",
                "B",
                labels=["blocked"],
                status={"name": "Done", "statusCategory": {"key": "done"}},
            ),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_jira_search_payload(issues))

        plan = get_provider("jira", client=_client(handler)).fetch_plan()
        statuses = {entity.entity_id: entity.status for entity in plan.entities}
        assert statuses["PAY-1"] == "blocked"
        assert statuses["PAY-2"] == "done"

    def test_paginates_until_is_last(self, jira_env) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = json.loads(request.content)
            if "nextPageToken" not in body:
                return httpx.Response(
                    200,
                    json=_jira_search_payload(
                        [_issue("PAY-1", "One")], next_token="t2"
                    ),
                )
            return httpx.Response(200, json=_jira_search_payload([_issue("PAY-2", "Two")]))

        plan = get_provider("jira", client=_client(handler)).fetch_plan()
        assert calls["n"] == 2
        assert {e.entity_id for e in plan.entities} == {"PAY-1", "PAY-2"}

    def test_falls_back_to_the_legacy_search_endpoint(self, jira_env) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/rest/api/3/search/jql":
                return httpx.Response(404, json={"errorMessages": ["gone"]})
            return httpx.Response(
                200, json={"issues": [_issue("PAY-1", "One")], "total": 1}
            )

        plan = get_provider("jira", client=_client(handler)).fetch_plan()
        assert "/rest/api/3/search" in seen
        assert [e.entity_id for e in plan.entities] == ["PAY-1"]

    def test_out_of_scope_links_are_stubbed_by_default(self, jira_env) -> None:
        issues = [
            _issue(
                "PAY-1",
                "One",
                issuelinks=[
                    {"type": {"name": "Blocks"}, "inwardIssue": {"key": "OTHER-9"}}
                ],
            )
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_jira_search_payload(issues))

        plan = get_provider("jira", client=_client(handler)).fetch_plan()
        assert plan.out_of_scope == ["OTHER-9"]
        stub = next(e for e in plan.entities if e.entity_id == "OTHER-9")
        assert stub.metadata["out_of_scope"] is True

    def test_out_of_scope_links_can_be_fatal(self, jira_env, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_OUT_OF_SCOPE_LINKS", "error")
        issues = [
            _issue(
                "PAY-1",
                "One",
                issuelinks=[
                    {"type": {"name": "Blocks"}, "inwardIssue": {"key": "OTHER-9"}}
                ],
            )
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_jira_search_payload(issues))

        with pytest.raises(IntegrationError, match="not part of the"):
            get_provider("jira", client=_client(handler)).fetch_plan()

    def test_http_errors_are_wrapped_and_redacted(self, jira_env) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"errorMessages": ["boom"]})

        with pytest.raises(IntegrationError, match="HTTP 500"):
            get_provider("jira", client=_client(handler)).fetch_plan()

    def test_transport_failure_is_wrapped(self, jira_env) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with pytest.raises(IntegrationError, match="jira request failed"):
            get_provider("jira", client=_client(handler)).fetch_plan()


class TestJiraPush:
    def test_reassignment_puts_the_assignee(self, jira_env) -> None:
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            return httpx.Response(204)

        provider = get_provider("jira", client=_client(handler))
        provider.push_changes(
            {}, [{"action": "reassign", "entity_id": "PAY-1", "resource_id": "acc-9"}], "reassignment"
        )
        assert calls == [("PUT", "/rest/api/3/issue/PAY-1/assignee")]

    def test_add_dependency_posts_a_block_link(self, jira_env) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(201)

        provider = get_provider("jira", client=_client(handler))
        provider.push_changes(
            {},
            [{"action": "add_dependency", "dependent": "A", "prerequisite": "B"}],
            "recovery",
        )
        assert captured["path"] == "/rest/api/3/issueLink"
        assert captured["body"]["type"]["name"] == "Blocks"
        assert captured["body"]["outwardIssue"]["key"] == "B"
        assert captured["body"]["inwardIssue"]["key"] == "A"

    def test_remove_dependency_finds_and_deletes_the_link(self, jira_env) -> None:
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "fields": {
                            "issuelinks": [
                                {
                                    "id": "555",
                                    "type": {"name": "Blocks"},
                                    "outwardIssue": {"key": "B"},
                                    "inwardIssue": {"key": "A"},
                                }
                            ]
                        }
                    },
                )
            return httpx.Response(204)

        provider = get_provider("jira", client=_client(handler))
        provider.push_changes(
            {},
            [{"action": "remove_dependency", "dependent": "A", "prerequisite": "B"}],
            "recovery",
        )
        assert calls == [
            ("GET", "/rest/api/3/issue/A"),
            ("DELETE", "/rest/api/3/issueLink/555"),
        ]

    def test_remove_dependency_is_a_noop_when_the_link_is_absent(self, jira_env) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            return httpx.Response(200, json={"fields": {"issuelinks": []}})

        provider = get_provider("jira", client=_client(handler))
        provider.push_changes(
            {},
            [{"action": "remove_dependency", "dependent": "A", "prerequisite": "B"}],
            "recovery",
        )
        assert calls == ["GET"]

    def test_unsupported_action_is_refused(self, jira_env) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        provider = get_provider("jira", client=_client(handler))
        with pytest.raises(IntegrationError, match="does not support"):
            provider.push_changes({}, [{"action": "teleport"}], "recovery")


# ── PagerDuty ─────────────────────────────────────────────────────────────────


class TestPagerDuty:
    def test_service_map_parsing(self) -> None:
        assert parse_service_map("P1:payment-api, P2:auth") == {
            "P1": "payment-api",
            "P2": "auth",
        }
        assert parse_service_map("") == {}

    def test_invalid_service_map_is_rejected(self) -> None:
        with pytest.raises(IntegrationError, match="PAGERDUTY_SERVICE_MAP"):
            parse_service_map("just-an-id")

    def test_ingest_maps_severity_and_flags_unmapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-secret")
        monkeypatch.setenv("PAGERDUTY_SERVICE_MAP", "SVC1:payment-api")
        scenario_id = f"pagerduty-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("PAGERDUTY_SCENARIO_ID", scenario_id)
        tag = uuid.uuid4().hex[:6]

        incidents = [
            {
                "id": f"I1-{tag}",
                "title": "Gateway down",
                "severity": "critical",
                "service": {"id": "SVC1"},
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": f"I2-{tag}",
                "title": "Noise",
                "severity": "warning",
                "service": {"id": "SVC-UNKNOWN"},
                "created_at": "2026-01-02T00:00:00+00:00",
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/incidents"
            return httpx.Response(
                200, json={"incidents": incidents, "has_more": False}
            )

        provider = PagerDutyProvider(
            client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url="https://api.pagerduty.com",
            )
        )
        result = provider.ingest()
        assert result["fetched"] == 2
        assert result["stored"] == 2
        assert result["unmapped"] == 1

        rows = _incidents_for(f"pagerduty:{sanitize_id(scenario_id)}")
        severities = {row.external_id: row.severity for row in rows}
        assert severities == {f"I1-{tag}": "critical", f"I2-{tag}": "medium"}
    def test_ingest_is_idempotent_on_external_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-secret")
        monkeypatch.setenv("PAGERDUTY_SERVICE_MAP", "SVC1:payment-api")
        monkeypatch.setenv("PAGERDUTY_SCENARIO_ID", f"pd-{uuid.uuid4().hex[:8]}")
        incident = {
            "id": f"I1-{uuid.uuid4().hex[:8]}",
            "title": "Once",
            "severity": "error",
            "service": {"id": "SVC1"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"incidents": [incident], "has_more": False})

        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.pagerduty.com"
        )
        first = PagerDutyProvider(client=client).ingest()
        second = PagerDutyProvider(client=client).ingest()
        assert first["stored"] == 1
        assert second["stored"] == 0

    def test_incidents_are_read_only(self) -> None:
        provider = PagerDutyProvider(client=httpx.Client())
        with pytest.raises(IntegrationError, match="read-only"):
            provider.push_changes({}, [], "recovery")
        with pytest.raises(IntegrationError, match="not a plan provider"):
            provider.fetch_plan()


# ── outbox worker ─────────────────────────────────────────────────────────────


class _FakeProvider:
    name = "jira"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[Any] = []
        self.fail = fail
        self.closed = False

    def push_changes(self, scenario, changes, kind) -> None:
        if self.fail:
            raise IntegrationError("upstream exploded")
        self.calls.append((scenario["scenario_id"], changes, kind))

    def close(self) -> None:
        self.closed = True


def _queued_commit(monkeypatch: pytest.MonkeyPatch) -> int:
    """Commit a reassignment with write-back enabled, returning the commit id."""
    from rippleback import repository as repo
    from rippleback.commit import commit_reassignment, dry_run_commit
    from rippleback.db import session_scope as scope
    from rippleback.integrations import outbox

    monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
    monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "jira")
    monkeypatch.setenv("RIPPLEBACK_PM_WRITEBACK_ENABLED", "true")
    monkeypatch.setattr(outbox, "kick", lambda: None)  # never run the worker here

    sid = f"outbox-{uuid.uuid4().hex[:12]}"
    scenario = {
        "scenario_id": sid,
        "plan_id": f"plan-{sid}",
        "version": 1,
        "status": "healthy",
        "entities": [
            {"id": "a", "name": "A", "status": "in_progress"},
            {"id": "b", "name": "B", "status": "pending", "assignee": "r1"},
        ],
        "resources": [
            {"id": "r1", "name": "R1", "capacity": 1},
            {"id": "r2", "name": "R2", "capacity": 1},
        ],
        "dependencies": [{"dependent": "b", "prerequisite": "a"}],
        "constraints": [],
    }
    with scope() as session:
        repo.save_version(session, scenario, origin="test", version=1, source="test")

    change = {"action": "reassign", "entity_id": "b", "resource_id": "r2"}
    report = dry_run_commit(
        sid, expected_version=1, changes=[change], kind="reassignment"
    )
    result = commit_reassignment(
        sid,
        expected_version=1,
        confirm_token=report["confirm_token"],
        changes=[change],
        idempotency_key=f"outbox-{sid}",
    )
    assert result["status"] == "writeback_pending"
    return result["commit_id"]


class TestOutboxWorker:
    def test_successful_push_marks_commit_pushed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_id = _queued_commit(monkeypatch)
        fake = _FakeProvider()

        import rippleback.integrations as integrations

        monkeypatch.setattr(integrations, "get_provider", lambda name=None: fake)

        stats = process_outbox()
        assert stats["sent"] == 1
        assert stats["failed"] == 0
        assert fake.closed is True
        detail = repository.get_commit(commit_id)
        assert detail["status"] == "pushed"
        assert detail["outbox"]["status"] == "sent"

    def test_failure_is_retried_then_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_id = _queued_commit(monkeypatch)
        fake = _FakeProvider(fail=True)

        import rippleback.integrations as integrations

        monkeypatch.setattr(integrations, "get_provider", lambda name=None: fake)
        monkeypatch.setattr("rippleback.integrations.outbox.MAX_ATTEMPTS", 2)
        notified: list[tuple[str, Any]] = []
        monkeypatch.setattr(
            "rippleback.notify.notify_event",
            lambda event, data: notified.append((event, data)),
        )

        first = process_outbox()
        assert first["failed"] == 1
        assert repository.get_commit(commit_id)["status"] == "writeback_pending"
        assert repository.get_commit(commit_id)["outbox"]["attempts"] == 1

        second = process_outbox()
        assert second["failed"] == 1
        detail = repository.get_commit(commit_id)
        assert detail["status"] == "writeback_failed"
        assert detail["outbox"]["status"] == "failed"
        assert any(event == "writeback_failed" for event, _ in notified)

    def test_secrets_in_provider_errors_are_redacted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_id = _queued_commit(monkeypatch)
        secret = "sk-live-outbox-secret"
        monkeypatch.setenv("RIPPLEBACK_API_KEY", secret)

        class _LeakyProvider:
            name = "jira"

            def push_changes(self, *args, **kwargs) -> None:
                raise IntegrationError(f"auth failed with {secret}")

            def close(self) -> None:
                pass

        import rippleback.integrations as integrations

        monkeypatch.setattr(
            integrations, "get_provider", lambda name=None: _LeakyProvider()
        )
        process_outbox()
        detail = repository.get_commit(commit_id)
        assert secret not in json.dumps(detail)

    def test_retry_requeues_a_failed_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_id = _queued_commit(monkeypatch)
        fake = _FakeProvider(fail=True)
        import rippleback.integrations as integrations

        monkeypatch.setattr(integrations, "get_provider", lambda name=None: fake)
        monkeypatch.setattr("rippleback.integrations.outbox.kick", lambda: None)
        process_outbox()
        result = retry_commit(commit_id)
        assert result["requeued"] is True
        assert repository.get_commit(commit_id)["outbox"]["status"] == "pending"
        assert repository.get_commit(commit_id)["outbox"]["attempts"] == 0

    def test_retry_of_a_pushed_commit_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_id = _queued_commit(monkeypatch)
        fake = _FakeProvider()
        import rippleback.integrations as integrations

        monkeypatch.setattr(integrations, "get_provider", lambda name=None: fake)
        monkeypatch.setattr("rippleback.integrations.outbox.kick", lambda: None)
        process_outbox()
        assert retry_commit(commit_id)["requeued"] is False

    def test_retry_of_an_unknown_commit_reports_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("rippleback.integrations.outbox.kick", lambda: None)
        result = retry_commit(10**9)
        assert result == {"requeued": False, "commit_id": 10**9, "status": "unknown"}
