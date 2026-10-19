"""Production write path: gates, confirm tokens, idempotency, disruptions, audit.

Every test materializes its **own** DB-only scenario (a unique ``scenario_id``
per test) so nothing here can disturb the seeded demo fixtures the integration
tests assert on, and no test depends on another's leftovers.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from rippleback import repository
from rippleback.commit import (
    CommitError,
    commit_reassignment,
    commit_recovery,
    dry_run_commit,
    record_disruption,
    resolve_disruption,
)
from rippleback.db import AuditLog, Disruption, RecoveryCommit, session_scope
from rippleback.scenarios import ScenarioError


def _mini_scenario(sid: str) -> dict[str, Any]:
    return {
        "scenario_id": sid,
        "plan_id": f"plan-{sid}",
        "version": 1,
        "status": "healthy",
        "entities": [
            {"id": "a", "name": "A", "status": "in_progress"},
            {"id": "b", "name": "B", "status": "pending", "assignee": "r1"},
            {"id": "c", "name": "C", "status": "done", "assignee": "r1"},
        ],
        "resources": [
            {"id": "r1", "name": "R1", "capacity": 1},
            {"id": "r2", "name": "R2", "capacity": 1},
        ],
        "dependencies": [{"dependent": "b", "prerequisite": "a"}],
        "constraints": [],
    }


@pytest.fixture
def scenario():
    """Materialize a fresh DB-only scenario (writes left as configured)."""
    sid = f"write-{uuid.uuid4().hex[:12]}"
    data = _mini_scenario(sid)
    with session_scope() as session:
        repository.save_version(session, data, origin="test", version=1, source="test")
    return data


@pytest.fixture
def writable(scenario, monkeypatch: pytest.MonkeyPatch):
    """The same fresh scenario, with writes enabled for one test."""
    monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
    return scenario


DROP = {
    "action": "remove_dependency",
    "dependent": "b",
    "prerequisite": "a",
}
CYCLE = {
    "action": "add_dependency",
    "dependent": "a",
    "prerequisite": "b",
}


# ── Gating ────────────────────────────────────────────────────────────────────


class TestWritesDisabled:
    def test_dry_run_is_refused_when_writes_are_disabled(self, scenario) -> None:
        with pytest.raises(CommitError, match="writes are disabled"):
            dry_run_commit(scenario["scenario_id"], expected_version=1, changes=[DROP])

    def test_commit_is_refused_when_writes_are_disabled(self, scenario) -> None:
        with pytest.raises(CommitError, match="writes are disabled"):
            commit_recovery(
                scenario["scenario_id"],
                expected_version=1,
                confirm_token="1.abc",
                changes=[DROP],
                idempotency_key="k1",
            )

    def test_disruption_is_refused_when_writes_are_disabled(self, scenario) -> None:
        with pytest.raises(CommitError, match="writes are disabled"):
            record_disruption(
                scenario["scenario_id"], entity_id="a", idempotency_key="k1"
            )

    def test_nothing_was_written(self, scenario) -> None:
        with session_scope() as session:
            rows = session.execute(
                select(RecoveryCommit).where(
                    RecoveryCommit.scenario_id == scenario["scenario_id"]
                )
            ).scalars().all()
        assert rows == []
        assert repository.get_scenario(scenario["scenario_id"])["version"] == 1


# ── Dry run ───────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_diffs_without_mutating(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        assert report["feasible"] is True
        assert report["from_version"] == 1
        assert report["to_version"] == 2
        assert report["confirm_token"].count(".") == 1
        assert report["diff"]["removed_dependencies"] == [
            {"dependent": "b", "prerequisite": "a"}
        ]
        after = repository.get_scenario(scenario_id)
        assert after["version"] == 1
        assert {"dependent": "b", "prerequisite": "a"} in after["dependencies"]

    def test_dry_run_rejects_stale_version(self, writable) -> None:
        with pytest.raises(CommitError, match="stale expected_plan_version"):
            dry_run_commit(
                writable["scenario_id"], expected_version=99, changes=[DROP]
            )

    def test_dry_run_rejects_infeasible_change(self, writable) -> None:
        with pytest.raises(CommitError, match="infeasible"):
            dry_run_commit(writable["scenario_id"], expected_version=1, changes=[CYCLE])

    def test_dry_run_rejects_substitute_option_id(self, writable) -> None:
        with pytest.raises(CommitError, match="explicit changes"):
            dry_run_commit(
                writable["scenario_id"],
                expected_version=1,
                option_id="substitute:a->b",
            )

    def test_dry_run_accepts_a_matching_drop_option_id(self, writable) -> None:
        report = dry_run_commit(
            writable["scenario_id"],
            expected_version=1,
            option_id="drop-dependency:b->a",
        )
        assert report["changes"] == [DROP]


# ── Commit + idempotency ──────────────────────────────────────────────────────


class TestCommit:
    def test_dry_run_then_commit_applies_and_advances(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        result = commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-1",
        )
        assert result["replayed"] is False
        assert result["from_version"] == 1
        assert result["to_version"] == 2
        assert result["status"] == "local_only"  # write-back disabled by default
        after = repository.get_scenario(scenario_id)
        assert after["version"] == 2
        assert {"dependent": "b", "prerequisite": "a"} not in after["dependencies"]

    def test_replaying_the_same_key_returns_the_stored_result(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        first = commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-replay",
        )
        second = commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-replay",
        )
        assert second["replayed"] is True
        assert second["commit_id"] == first["commit_id"]
        assert second["to_version"] == first["to_version"]
        # replay must not re-resolve or advance again
        assert repository.get_scenario(scenario_id)["version"] == 2

    def test_reused_key_with_a_different_request_is_rejected(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-dup",
        )
        with pytest.raises(CommitError, match="different request"):
            commit_recovery(
                scenario_id,
                expected_version=1,
                confirm_token=report["confirm_token"],
                changes=[DROP],
                option_id="drop-dependency:b->a",  # same effect, different raw request
                idempotency_key="commit-dup",
            )

    def test_stale_version_after_commit_is_refused(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-stale-1",
        )
        # version is now 2, so anything still expecting 1 is stale
        with pytest.raises(CommitError, match="stale expected_plan_version"):
            dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        with pytest.raises(CommitError, match="stale expected_plan_version"):
            commit_recovery(
                scenario_id,
                expected_version=1,
                confirm_token=report["confirm_token"],
                changes=[DROP],
                idempotency_key="commit-stale-2",
            )

    def test_wrong_confirm_token_is_refused(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        with pytest.raises(CommitError, match="does not match"):
            commit_recovery(
                scenario_id,
                expected_version=1,
                confirm_token=report["confirm_token"],
                changes=[CYCLE],  # token was issued for a different change set
                idempotency_key="commit-bad-token",
            )
        assert repository.get_scenario(scenario_id)["version"] == 1

    def test_expired_confirm_token_is_refused(
        self, writable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario_id = writable["scenario_id"]
        monkeypatch.setenv("RIPPLEBACK_CONFIRM_TOKEN_TTL_SECONDS", "1")
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        time.sleep(2.1)
        with pytest.raises(CommitError, match="expired"):
            commit_recovery(
                scenario_id,
                expected_version=1,
                confirm_token=report["confirm_token"],
                changes=[DROP],
                idempotency_key="commit-expired",
            )

    def test_missing_idempotency_key_is_refused(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        with pytest.raises(CommitError, match="idempotency_key"):
            commit_recovery(
                scenario_id,
                expected_version=1,
                confirm_token=report["confirm_token"],
                changes=[DROP],
                idempotency_key="",
            )

    def test_commit_writes_an_audit_row(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-audit",
            actor="tester",
        )
        rows = repository.list_audit(scenario_id=scenario_id, limit=10)
        commit_rows = [row for row in rows if row["action"] == "commit_recovery"]
        assert commit_rows and commit_rows[0]["outcome"] == "ok"
        assert commit_rows[0]["actor"] == "tester"


class TestReassignment:
    def test_reassignment_moves_the_assignee(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        change = {"action": "reassign", "entity_id": "b", "resource_id": "r2"}
        report = dry_run_commit(
            scenario_id, expected_version=1, changes=[change], kind="reassignment"
        )
        result = commit_reassignment(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[change],
            idempotency_key="reassign-1",
        )
        assert result["to_version"] == 2
        after = repository.get_scenario(scenario_id)
        assert next(e for e in after["entities"] if e["id"] == "b")["assignee"] == "r2"

    def test_reassignment_to_current_resource_is_refused(self, writable) -> None:
        change = {"action": "reassign", "entity_id": "b", "resource_id": "r1"}
        with pytest.raises(CommitError, match="nothing would change"):
            dry_run_commit(
                writable["scenario_id"],
                expected_version=1,
                changes=[change],
                kind="reassignment",
            )


# ── Disruptions ───────────────────────────────────────────────────────────────


class TestDisruptions:
    def test_record_blocks_entity_and_disrupts_plan(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        result = record_disruption(
            scenario_id,
            entity_id="a",
            event_type="service_outage",
            severity="critical",
            idempotency_key="disrupt-1",
        )
        assert result["replayed"] is False
        assert result["previous_entity_status"] == "in_progress"
        assert result["entity_status"] == "blocked"
        assert result["plan_version"] == 2
        after = repository.get_scenario(scenario_id)
        assert after["status"] == "disrupted"
        assert next(e for e in after["entities"] if e["id"] == "a")["status"] == "blocked"
        assert repository.list_open_disruptions(scenario_id)[0]["id"] == result["disruption_id"]

    def test_done_entity_is_never_reblocked(self, writable) -> None:
        result = record_disruption(
            writable["scenario_id"],
            entity_id="c",  # status "done"
            idempotency_key="disrupt-done",
        )
        assert result["entity_status"] == "done"
        assert result["previous_entity_status"] == "done"

    def test_resolve_restores_prior_statuses(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        recorded = record_disruption(
            scenario_id, entity_id="a", idempotency_key="disrupt-resolve"
        )
        resolved = resolve_disruption(
            recorded["disruption_id"], idempotency_key="resolve-1"
        )
        assert resolved["replayed"] is False
        assert resolved["status"] == "resolved"
        after = repository.get_scenario(scenario_id)
        assert after["status"] == "healthy"
        assert next(e for e in after["entities"] if e["id"] == "a")["status"] == "in_progress"

    def test_resolving_twice_is_an_idempotent_no_op(self, writable) -> None:
        recorded = record_disruption(
            writable["scenario_id"], entity_id="a", idempotency_key="disrupt-replay"
        )
        first = resolve_disruption(recorded["disruption_id"])
        second = resolve_disruption(recorded["disruption_id"])
        assert first["replayed"] is False
        assert second["replayed"] is True

    def test_disruption_requires_idempotency_key(self, writable) -> None:
        with pytest.raises(CommitError, match="idempotency_key"):
            record_disruption(writable["scenario_id"], entity_id="a", idempotency_key="")

    def test_delay_event_requires_delay_minutes(self, writable) -> None:
        with pytest.raises(CommitError, match="requires delay_minutes"):
            record_disruption(
                writable["scenario_id"],
                entity_id="a",
                event_type="delivery_delay",
                idempotency_key="disrupt-delay",
            )

    def test_unknown_entity_and_event_and_severity_are_refused(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        with pytest.raises(CommitError, match="unknown entity_id"):
            record_disruption(scenario_id, entity_id="ghost", idempotency_key="d1")
        with pytest.raises(CommitError, match="unknown event_type"):
            record_disruption(
                scenario_id, entity_id="a", event_type="meteor", idempotency_key="d2"
            )
        with pytest.raises(CommitError, match="unknown severity"):
            record_disruption(
                scenario_id, entity_id="a", severity="apocalyptic", idempotency_key="d3"
            )

    def test_resolving_unknown_id_is_refused(self, writable) -> None:
        with pytest.raises(CommitError, match="unknown disruption_id"):
            resolve_disruption(10**9)

    def test_recording_against_an_unknown_scenario_is_refused(self, writable) -> None:
        with pytest.raises(ScenarioError, match="unknown scenario_id"):
            record_disruption("no-such-scenario", entity_id="a", idempotency_key="d4")


# ── Redaction ─────────────────────────────────────────────────────────────────


class TestRedaction:
    def test_secret_in_a_disruption_reason_is_never_stored(
        self, writable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "sk-live-abcdef0123456789"
        monkeypatch.setenv("RIPPLEBACK_API_KEY", secret)
        result = record_disruption(
            writable["scenario_id"],
            entity_id="a",
            reason=f"gateway failed because {secret} was pasted in the ticket",
            actor=f"operator using {secret}",
            idempotency_key="disrupt-redact",
        )
        with session_scope() as session:
            row = session.get(Disruption, result["disruption_id"])
            stored = f"{row.reason} {row.actor}"
        assert secret not in stored
        assert "[redacted]" in stored

    def test_secret_shaped_values_are_redacted_even_without_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rippleback.security import redact

        monkeypatch.delenv("RIPPLEBACK_API_KEY", raising=False)
        for shaped in (
            "Authorization: Bearer abcdefghijklmnop",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_0123456789abcdefghijklmnopqrstuvwx",
            "https://hooks.slack.com/xoxb-1234567890-abcdefghij",
        ):
            assert "abcdefghij" not in redact(shaped) or "[redacted]" in redact(shaped)
        assert "Bearer abcdefghijklmnop" not in redact("Authorization: Bearer abcdefghijklmnop")

    def test_audit_detail_is_redacted(
        self, writable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "tok-audit-9876543210"
        monkeypatch.setenv("GITHUB_TOKEN", secret)
        result = record_disruption(
            writable["scenario_id"],
            entity_id="a",
            reason=f"leaked {secret}",
            idempotency_key="disrupt-audit-redact",
        )
        rows = repository.list_audit(scenario_id=writable["scenario_id"], limit=20)
        assert any(row["action"] == "record_disruption" for row in rows)
        for row in rows:
            assert secret not in str(row)


class TestAuditAndCommitListing:
    def test_commit_listing_includes_the_outbox_state(self, writable) -> None:
        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        committed = commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-list",
        )
        listing = repository.list_commits(scenario_id=scenario_id)
        assert listing[0]["id"] == committed["commit_id"]
        assert listing[0]["status"] == "local_only"
        detail = repository.get_commit(committed["commit_id"])
        assert detail is not None
        assert detail["outbox"] is None  # write-back disabled
        assert detail["changes"] == [DROP]

    def test_get_commit_returns_none_for_unknown_id(self) -> None:
        assert repository.get_commit(10**9) is None


class TestWritebackQueue:
    def test_writeback_queues_an_outbox_row(
        self, writable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rippleback.integrations import outbox

        monkeypatch.setenv("RIPPLEBACK_PM_PROVIDER", "jira")
        monkeypatch.setenv("RIPPLEBACK_PM_WRITEBACK_ENABLED", "true")
        monkeypatch.setattr(outbox, "kick", lambda: None)  # never run the worker here

        scenario_id = writable["scenario_id"]
        report = dry_run_commit(scenario_id, expected_version=1, changes=[DROP])
        committed = commit_recovery(
            scenario_id,
            expected_version=1,
            confirm_token=report["confirm_token"],
            changes=[DROP],
            idempotency_key="commit-writeback",
        )
        assert committed["status"] == "writeback_pending"
        detail = repository.get_commit(committed["commit_id"])
        assert detail is not None
        assert detail["outbox"]["provider"] == "jira"
        assert detail["outbox"]["status"] == "pending"

