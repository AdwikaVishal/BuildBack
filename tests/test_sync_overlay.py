"""PM sync: overlaid local commits, idempotent re-sync, failure reporting.

The provider is faked at the ``sync`` module seam, so no HTTP is involved; the
scenario shape is produced by the real :func:`build_scenario`, guaranteeing the
content-hash comparisons behave exactly as in production.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from rippleback import repository, sync
from rippleback.db import RecoveryCommit, SyncState, session_scope
from rippleback.integrations.base import (
    ImportedDependency,
    ImportedEntity,
    ImportedPlan,
    build_scenario,
)

ENTITIES = [
    ("a", "A", "in_progress"),
    ("b", "B", "pending"),
    ("c", "C", "done"),
]


def _plan(sid: str, dependencies: list[tuple[str, str]]) -> ImportedPlan:
    return ImportedPlan(
        provider="jira",
        scenario_id=sid,
        plan_id=f"jira-{sid}",
        external_id="PAY",
        entities=[
            ImportedEntity(entity_id=e, name=n, status=s) for e, n, s in ENTITIES
        ],
        dependencies=[ImportedDependency(d, p) for d, p in dependencies],
    )


class FakeProvider:
    name = "jira"

    def __init__(self, plan: ImportedPlan) -> None:
        self._plan = plan
        self.closed = False

    def require_configured(self) -> None:
        return None

    def fetch_plan(self) -> ImportedPlan:
        return self._plan

    def close(self) -> None:
        self.closed = True


def _install_provider(monkeypatch: pytest.MonkeyPatch, plan: ImportedPlan) -> FakeProvider:
    provider = FakeProvider(plan)
    monkeypatch.setattr(sync, "get_provider", lambda name=None, client=None: provider)
    return provider


@pytest.fixture
def synced_scenario(monkeypatch: pytest.MonkeyPatch):
    """A DB scenario seeded from an imported plan (a <- b)."""
    sid = f"sync-{uuid.uuid4().hex[:12]}"
    plan = _plan(sid, [("b", "a")])
    scenario = build_scenario(plan)
    with session_scope() as session:
        repository.save_version(session, scenario, origin="sync", version=1, source="jira")
    return sid


def _commit_drop(sid: str, monkeypatch: pytest.MonkeyPatch) -> int:
    from rippleback.commit import commit_recovery, dry_run_commit
    from rippleback.integrations import outbox

    monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
    monkeypatch.setattr(outbox, "kick", lambda: None)
    change = {"action": "remove_dependency", "dependent": "b", "prerequisite": "a"}
    report = dry_run_commit(sid, expected_version=1, changes=[change])
    result = commit_recovery(
        sid,
        expected_version=1,
        confirm_token=report["confirm_token"],
        changes=[change],
        idempotency_key=f"sync-{sid}",
    )
    return result["commit_id"]


class TestSyncPlan:
    def test_first_sync_stores_the_plan(self, monkeypatch) -> None:
        sid = f"sync-{uuid.uuid4().hex[:12]}"
        plan = _plan(sid, [("b", "a")])
        provider = _install_provider(monkeypatch, plan)
        result = sync.sync_plan()
        assert result["ok"] is True
        assert result["status"] == "ok"
        assert result["version"] == 1
        assert provider.closed is True
        assert repository.get_scenario(sid)["dependencies"] == [
            {"dependent": "b", "prerequisite": "a"}
        ]

    def test_re_syncing_unchanged_data_does_not_advance(self, monkeypatch) -> None:
        sid = f"sync-{uuid.uuid4().hex[:12]}"
        _install_provider(monkeypatch, _plan(sid, [("b", "a")]))
        assert sync.sync_plan()["status"] == "ok"
        second = sync.sync_plan()
        assert second["status"] == "unchanged"
        assert second["version"] == 1

    def test_local_commit_is_re_applied_over_unreflected_import(
        self, synced_scenario, monkeypatch
    ) -> None:
        sid = synced_scenario
        commit_id = _commit_drop(sid, monkeypatch)
        # PM still reports the dependency the local commit removed
        _install_provider(monkeypatch, _plan(sid, [("b", "a")]))

        result = sync.sync_plan()
        assert result["ok"] is True
        assert result["status"] == "unchanged"
        assert result["retired_overlays"] == 0
        # the overlay survives the sync
        assert repository.get_scenario(sid)["dependencies"] == []
        with session_scope() as session:
            commit = session.get(RecoveryCommit, commit_id)
            assert commit.status == "local_only"

    def test_overlay_is_retired_once_the_pm_reflects_it(
        self, synced_scenario, monkeypatch
    ) -> None:
        sid = synced_scenario
        commit_id = _commit_drop(sid, monkeypatch)
        # PM now reports the dependency as removed
        _install_provider(monkeypatch, _plan(sid, []))

        result = sync.sync_plan()
        assert result["ok"] is True
        assert result["retired_overlays"] == 1
        with session_scope() as session:
            commit = session.get(RecoveryCommit, commit_id)
            assert commit.status == repository.SYNCED_BACK_STATUS

    def test_sync_writes_state_and_audit(self, monkeypatch) -> None:
        sid = f"sync-{uuid.uuid4().hex[:12]}"
        _install_provider(monkeypatch, _plan(sid, [("b", "a")]))
        sync.sync_plan()
        with session_scope() as session:
            state = session.get(SyncState, sid)
            assert state is not None
            assert state.last_status == "ok"
            assert state.provider == "jira"
        audit = repository.list_audit(scenario_id=sid, limit=10)
        assert any(row["action"] == "sync_plan" for row in audit)

    def test_scenario_id_mismatch_is_reported(self, monkeypatch) -> None:
        _install_provider(monkeypatch, _plan("other-scenario", [("b", "a")]))
        result = sync.sync_plan("something-else")
        assert result["ok"] is False
        assert "does not match" in result["error"]


class TestSyncFailures:
    def test_provider_error_is_recorded_not_raised(self, monkeypatch) -> None:
        from rippleback.integrations import IntegrationError

        class Boom:
            name = "jira"
            missing_env = ()

            def require_configured(self) -> None:
                raise IntegrationError("jira integration is not configured; missing: JIRA_API_TOKEN")

            def close(self) -> None:
                pass

        monkeypatch.setattr(sync, "get_provider", lambda name=None, client=None: Boom())
        sid = f"sync-{uuid.uuid4().hex[:12]}"
        result = sync.sync_plan(sid)
        assert result["ok"] is False
        assert result["missing"] == []
        assert "not configured" in result["error"]
        with session_scope() as session:
            state = session.get(SyncState, sid)
            assert state is not None and state.last_status == "error"
        audit = repository.list_audit(scenario_id=sid, limit=10)
        assert any(row["outcome"] == "error" for row in audit)

    def test_failure_message_is_redacted(self, monkeypatch) -> None:
        from rippleback.integrations import IntegrationError

        secret = "sk-live-sync-secret"
        monkeypatch.setenv("JIRA_API_TOKEN", secret)

        class Boom:
            name = "jira"
            missing_env = ()

            def require_configured(self) -> None:
                raise IntegrationError(f"auth failed with token {secret}")

            def close(self) -> None:
                pass

        monkeypatch.setattr(sync, "get_provider", lambda name=None, client=None: Boom())
        result = sync.sync_plan("redact-scenario")
        assert secret not in json.dumps(result)

    def test_notify_is_called_on_failure(self, monkeypatch) -> None:
        events: list[tuple[str, Any]] = []
        monkeypatch.setattr(
            "rippleback.notify.notify_event",
            lambda event, data: events.append((event, data)),
        )

        class Boom:
            name = "jira"
            missing_env = ()

            def require_configured(self) -> None:
                from rippleback.integrations import IntegrationError

                raise IntegrationError("nope")

            def close(self) -> None:
                pass

        monkeypatch.setattr(sync, "get_provider", lambda name=None, client=None: Boom())
        sync.sync_plan("notify-scenario")
        assert any(event == "sync_failed" for event, _ in events)


class TestSyncAll:
    def test_no_provider_reports_the_missing_variable(self, monkeypatch) -> None:
        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "none")
        monkeypatch.delenv("PAGERDUTY_API_KEY", raising=False)
        result = sync.sync_all()
        assert result["ok"] is False
        assert result["plan"]["missing"] == ["RIPPLEBACK_PM_PROVIDER"]

    def test_start_sync_loop_is_a_noop_when_unconfigured(self, monkeypatch) -> None:
        import threading

        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "none")
        assert sync.start_sync_loop(threading.Event()) is None

    def test_start_sync_loop_returns_a_thread_when_configured(self, monkeypatch) -> None:
        import threading

        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "jira")
        monkeypatch.setenv("RIPPLEBACK_SYNC_INTERVAL_SECONDS", "3600")
        stop = threading.Event()
        thread = sync.start_sync_loop(stop)
        try:
            assert thread is not None and thread.is_alive()
        finally:
            stop.set()
