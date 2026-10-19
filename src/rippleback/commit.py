"""Gated, audited write path: disruptions, recovery commits, reassignments.

Flow (identical for recovery and reassignment):

1. ``*_dry_run`` loads the committed plan, rejects a stale
   ``expected_version``, applies the change set to a **copy**, runs the same
   hard-constraint/graph gate the read tools use, computes a deterministic
   diff, and returns a short-lived **confirm token** bound to
   ``(scenario_id, expected_version, exact change set)``.
2. ``commit_*`` requires that token, the same ``expected_version``, a fresh
   ``idempotency_key``, and ``RIPPLEBACK_WRITES_ENABLED=true``. It re-runs the
   gate, then writes the new plan version, the commit row, the audit row, and
   (when PM write-back is enabled) the outbox row in **one transaction**.
3. Replaying the same idempotency key with the same request returns the stored
   result and writes nothing; reusing it for a different request is an error.

The token secret is ``RIPPLEBACK_TOKEN_SECRET`` when set, otherwise a random
value generated once and stored in the ``app_settings`` table, so tokens work
across the MCP server and adapter processes that share one database.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rippleback import repository
from rippleback.config import load_settings
from rippleback.constraints import ConstraintError, validate_hard_constraints
from rippleback.db import (
    AppSetting,
    AuditLog,
    Disruption,
    Outbox,
    RecoveryCommit,
    canonical_json,
    content_hash,
    session_scope,
    utcnow_iso,
)
from rippleback.evaluation import EvaluationError, parse_option_id
from rippleback.scenarios import ScenarioError, validate_scenario
from rippleback.security import redact
from rippleback.triage import (
    VALID_EVENT_TYPES,
    TriageError,
    apply_changes as _apply_dependency_changes,
)

DROP_PREFIX = "drop-dependency:"
SUBSTITUTE_PREFIX = "substitute:"
KIND_RECOVERY = "recovery"
KIND_REASSIGNMENT = "reassignment"
VALID_SEVERITIES = ("low", "medium", "high", "critical")
DELAY_EVENT_TYPES = frozenset({"delivery_delay", "transport_delay"})
TOKEN_SECRET_KEY = "confirm_token_secret"


class CommitError(ValueError):
    """Raised when a write is refused, stale, malformed, or unconfirmed."""


# ── Gate helpers ──────────────────────────────────────────────────────────────


def _assert_writes_enabled() -> None:
    if not load_settings().writes_enabled:
        raise CommitError(
            "writes are disabled; set RIPPLEBACK_WRITES_ENABLED=true to record "
            "disruptions or commit changes"
        )


def _require_nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommitError(f"{name} must be a non-empty string")
    return value.strip()


def _expected_version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommitError("expected_plan_version must be an integer")
    return value


def _assert_version(scenario: dict[str, Any], expected: int) -> None:
    current = scenario["version"]
    if expected != current:
        raise CommitError(
            f"stale expected_plan_version: got {expected}, current is {current}; "
            "re-read the plan and try again"
        )


def _gate_recovery(scenario: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    try:
        report = validate_hard_constraints(scenario, changes=changes)
    except (ConstraintError, TriageError) as exc:
        raise CommitError(str(exc)) from exc
    if not report["feasible"]:
        codes = ", ".join(
            f"{violation.get('code') or violation.get('constraint_type')}"
            for violation in report["violations"]
        )
        raise CommitError(
            f"recovery is infeasible against the committed plan ({codes}); "
            "it would violate hard constraints or graph rules"
        )


def _validate_reassignment(
    scenario: dict[str, Any], changes: list[dict[str, Any]]
) -> None:
    resources = scenario.get("resources")
    if not resources:
        raise CommitError(
            "this scenario has no resources list, so reassignment is not "
            "available; use a staffed plan"
        )
    resource_ids = {resource["id"] for resource in resources}
    entities = {entity["id"]: entity for entity in scenario["entities"]}
    for index, change in enumerate(changes):
        entity_id = change.get("entity_id")
        resource_id = change.get("resource_id")
        if entity_id not in entities:
            raise CommitError(
                f"changes[{index}] references unknown entity_id {entity_id!r}"
            )
        if resource_id not in resource_ids:
            raise CommitError(
                f"changes[{index}] references unknown resource_id {resource_id!r}; "
                f"available: {sorted(resource_ids)}"
            )
        if entities[entity_id].get("assignee") == resource_id:
            raise CommitError(
                f"changes[{index}] assigns {entity_id!r} to its current resource "
                f"{resource_id!r}; nothing would change"
            )


def _gate(scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str) -> None:
    if kind == KIND_RECOVERY:
        _gate_recovery(scenario, changes)
    else:
        _validate_reassignment(scenario, changes)


# ── Change resolution and application ────────────────────────────────────────


def _option_changes(scenario: dict[str, Any], option_id: str) -> list[dict[str, Any]]:
    option_id = _require_nonempty(option_id, "option_id")
    if option_id.startswith(SUBSTITUTE_PREFIX):
        raise CommitError(
            f"option_id {option_id!r} uses the substitute id format; pass that "
            "option's explicit changes list instead"
        )
    try:
        dependent, prerequisite = parse_option_id(option_id)
    except EvaluationError as exc:
        raise CommitError(str(exc)) from exc
    entity_ids = {entity["id"] for entity in scenario["entities"]}
    for name in (dependent, prerequisite):
        if name not in entity_ids:
            raise CommitError(
                f"option_id {option_id!r} references unknown entity {name!r}"
            )
    if not any(
        dependency["dependent"] == dependent
        and dependency["prerequisite"] == prerequisite
        for dependency in scenario["dependencies"]
    ):
        raise CommitError(
            f"option_id {option_id!r} does not match a committed dependency edge"
        )
    return [
        {
            "action": "remove_dependency",
            "dependent": dependent,
            "prerequisite": prerequisite,
        }
    ]


def _normalize_changes(
    scenario: dict[str, Any], changes: Any, kind: str
) -> list[dict[str, Any]]:
    if not isinstance(changes, list) or not changes:
        raise CommitError("changes must be a non-empty list of change dicts")
    allowed = (
        {"remove_dependency", "add_dependency"}
        if kind == KIND_RECOVERY
        else {"reassign"}
    )
    normalized: list[dict[str, Any]] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise CommitError(f"changes[{index}] must be a dict")
        action = change.get("action")
        if action not in allowed:
            raise CommitError(
                f"changes[{index}] has action {action!r}; allowed: {sorted(allowed)}"
            )
        if action == "reassign":
            entry = {
                "action": "reassign",
                "entity_id": _require_nonempty(
                    change.get("entity_id"), f"changes[{index}].entity_id"
                ),
                "resource_id": _require_nonempty(
                    change.get("resource_id"), f"changes[{index}].resource_id"
                ),
            }
        else:
            entry = {
                "action": action,
                "dependent": _require_nonempty(
                    change.get("dependent"), f"changes[{index}].dependent"
                ),
                "prerequisite": _require_nonempty(
                    change.get("prerequisite"), f"changes[{index}].prerequisite"
                ),
            }
            if change.get("dependency_type") is not None:
                entry["dependency_type"] = _require_nonempty(
                    change.get("dependency_type"),
                    f"changes[{index}].dependency_type",
                )
            if change.get("constraint_parameters") is not None:
                parameters = change.get("constraint_parameters")
                if not isinstance(parameters, dict):
                    raise CommitError(
                        f"changes[{index}].constraint_parameters must be a dict"
                    )
                entry["constraint_parameters"] = dict(parameters)
        normalized.append(entry)
    return normalized


def resolve_changes(
    scenario: dict[str, Any],
    *,
    changes: Any = None,
    option_id: Any = None,
    kind: str = KIND_RECOVERY,
) -> list[dict[str, Any]]:
    """Normalize an explicit change list or a legacy ``drop-dependency`` id."""
    if changes is not None and option_id is not None:
        raise CommitError("pass either changes or option_id, not both")
    if option_id is not None:
        if kind != KIND_RECOVERY:
            raise CommitError("option_id applies to recovery commits only")
        resolved = _option_changes(scenario, _require_nonempty(option_id, "option_id"))
    elif changes is not None:
        resolved = _normalize_changes(scenario, changes, kind)
    else:
        raise CommitError("provide either changes or option_id")
    return resolved


def apply_change_set(
    scenario: dict[str, Any], changes: list[dict[str, Any]], *, kind: str
) -> dict[str, Any]:
    """Return a new scenario dict with ``changes`` applied (input untouched)."""
    result = copy.deepcopy(scenario)
    if kind == KIND_RECOVERY:
        result["dependencies"] = _apply_dependency_changes(result, changes)
        return result
    resources = {resource["id"]: resource for resource in result.get("resources", [])}
    entities = {entity["id"]: entity for entity in result["entities"]}
    for change in changes:
        entity = entities.get(change.get("entity_id"))
        resource_id = change.get("resource_id")
        if entity is None or resource_id not in resources:
            raise CommitError("reassignment references an unknown entity or resource")
        entity["assignee"] = resource_id
    return result


def is_reflected(
    scenario: dict[str, Any], changes: list[dict[str, Any]], *, kind: str
) -> bool:
    """True when imported data already contains a commit's effect."""
    if kind == KIND_REASSIGNMENT:
        entities = {entity["id"]: entity for entity in scenario["entities"]}
        return all(
            entities.get(change.get("entity_id"), {}).get("assignee")
            == change.get("resource_id")
            for change in changes
        )
    edges = {
        (dependency["dependent"], dependency["prerequisite"])
        for dependency in scenario["dependencies"]
    }
    for change in changes:
        edge = (change.get("dependent"), change.get("prerequisite"))
        if change.get("action") == "remove_dependency" and edge in edges:
            return False
        if change.get("action") == "add_dependency" and edge not in edges:
            return False
    return True


# ── Deterministic diff ───────────────────────────────────────────────────────


def diff_scenario(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Structured, ordered diff between two scenario snapshots."""
    before_edges = {
        (dependency["dependent"], dependency["prerequisite"]): dependency
        for dependency in before["dependencies"]
    }
    after_edges = {
        (dependency["dependent"], dependency["prerequisite"]): dependency
        for dependency in after["dependencies"]
    }
    removed = [
        before_edges[key]
        for key in sorted(before_edges)
        if key not in after_edges
    ]
    added = [after_edges[key] for key in sorted(after_edges) if key not in before_edges]
    before_entities = {entity["id"]: entity for entity in before["entities"]}
    reassigned: list[dict[str, Any]] = []
    status_changes: list[dict[str, Any]] = []
    for entity in after["entities"]:
        prior = before_entities.get(entity["id"])
        if prior is None:
            continue
        if prior.get("assignee") != entity.get("assignee"):
            reassigned.append(
                {
                    "entity_id": entity["id"],
                    "from": prior.get("assignee"),
                    "to": entity.get("assignee"),
                }
            )
        if prior.get("status") != entity.get("status"):
            status_changes.append(
                {
                    "entity_id": entity["id"],
                    "from": prior.get("status"),
                    "to": entity.get("status"),
                }
            )
    return {
        "removed_dependencies": removed,
        "added_dependencies": added,
        "reassigned": sorted(reassigned, key=lambda item: item["entity_id"]),
        "status_changes": sorted(status_changes, key=lambda item: item["entity_id"]),
        "version": {"from": before.get("version"), "to": after.get("version")},
    }


# ── Confirm tokens ───────────────────────────────────────────────────────────


def _token_secret() -> str:
    configured = load_settings().token_secret
    if configured:
        return configured
    with session_scope() as session:
        row = session.get(AppSetting, TOKEN_SECRET_KEY)
        if row is not None:
            return row.value
        value = secrets.token_urlsafe(32)
        session.add(
            AppSetting(key=TOKEN_SECRET_KEY, value=value, updated_at=utcnow_iso())
        )
        session.flush()
        return value


def _fingerprint(
    scenario_id: str, expected_version: int, changes: list[dict[str, Any]]
) -> str:
    return content_hash(
        {
            "scenario_id": scenario_id,
            "expected_plan_version": expected_version,
            "changes": changes,
        }
    )


def issue_confirm_token(
    scenario_id: str, expected_version: int, changes: list[dict[str, Any]]
) -> tuple[str, int]:
    """Return ``(token, expires_at_epoch_seconds)`` bound to this exact diff."""
    ttl = max(1, load_settings().confirm_token_ttl_seconds)
    expires_at = int(time.time()) + ttl
    message = f"{_fingerprint(scenario_id, expected_version, changes)}.{expires_at}"
    signature = hmac.new(
        _token_secret().encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{expires_at}.{signature}", expires_at


def verify_confirm_token(
    token: Any,
    scenario_id: str,
    expected_version: int,
    changes: list[dict[str, Any]],
) -> None:
    """Raise ``CommitError`` unless the token matches this exact diff and is fresh."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise CommitError(
            "a confirm_token from a dry run is required to commit; run the dry "
            "run again and pass its token"
        )
    raw_expiry, signature = token.split(".", 1)
    try:
        expires_at = int(raw_expiry)
    except ValueError as exc:
        raise CommitError("confirm_token is malformed; run the dry run again") from exc
    if expires_at < int(time.time()):
        raise CommitError("confirm_token has expired; run the dry run again")
    message = f"{_fingerprint(scenario_id, expected_version, changes)}.{expires_at}"
    expected = hmac.new(
        _token_secret().encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise CommitError(
            "confirm_token does not match this scenario, version, and change set; "
            "run the dry run again"
        )


# ── Audit ───────────────────────────────────────────────────────────────────


def _audit(
    session: Session,
    *,
    action: str,
    scenario_id: str | None,
    outcome: str,
    detail: Any,
    actor: str | None,
    request_id: str | None,
) -> None:
    session.add(
        AuditLog(
            at=utcnow_iso(),
            actor=redact(actor) if actor else None,
            action=action,
            scenario_id=scenario_id,
            outcome=outcome,
            detail=redact(canonical_json(detail)),
            request_id=request_id,
        )
    )


def audit_failure(
    *,
    action: str,
    scenario_id: str | None,
    detail: Any,
    actor: str | None = None,
    request_id: str | None = None,
) -> None:
    """Best-effort failure audit in its own transaction (never masks the error)."""
    try:
        with session_scope() as session:
            _audit(
                session,
                action=action,
                scenario_id=scenario_id,
                outcome="error",
                detail=detail,
                actor=actor,
                request_id=request_id,
            )
    except Exception:  # pragma: no cover - auditing must not break the caller
        pass


# ── Idempotency ─────────────────────────────────────────────────────────────


def _request_hash(payload: dict[str, Any]) -> str:
    return content_hash(payload)


def _replay_commit(session: Session, key: str, request_hash: str) -> RecoveryCommit | None:
    row = session.execute(
        select(RecoveryCommit).where(RecoveryCommit.idempotency_key == key)
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise CommitError(
            "idempotency_key was already used for a different request; use a new key"
        )
    return row


def _commit_response(row: RecoveryCommit, *, replayed: bool) -> dict[str, Any]:
    return {
        "scenario_id": row.scenario_id,
        "commit_id": row.id,
        "kind": row.kind,
        "option_id": row.option_id,
        "from_version": row.from_version,
        "to_version": row.to_version,
        "status": row.status,
        "changes": json.loads(row.changes),
        "diff": json.loads(row.diff),
        "created_at": row.created_at,
        "replayed": replayed,
    }


# ── Dry run ─────────────────────────────────────────────────────────────────


def dry_run_commit(
    scenario_id: str,
    *,
    expected_version: Any,
    changes: Any = None,
    option_id: Any = None,
    kind: str = KIND_RECOVERY,
) -> dict[str, Any]:
    """Validate and diff a proposed write without changing anything."""
    _assert_writes_enabled()
    expected = _expected_version(expected_version)
    scenario = repository.get_scenario(scenario_id)
    _assert_version(scenario, expected)
    resolved = resolve_changes(scenario, changes=changes, option_id=option_id, kind=kind)
    _gate(scenario, resolved, kind)
    after = apply_change_set(scenario, resolved, kind=kind)
    after["version"] = expected + 1
    diff = diff_scenario(scenario, after)
    token, expires_at = issue_confirm_token(scenario_id, expected, resolved)
    return {
        "scenario_id": scenario_id,
        "kind": kind,
        "from_version": expected,
        "to_version": expected + 1,
        "changes": resolved,
        "diff": diff,
        "feasible": True,
        "confirm_token": token,
        "expires_at": expires_at,
    }


# ── Commit ──────────────────────────────────────────────────────────────────


def _current_version(session: Session, scenario: dict[str, Any]) -> int:
    latest = repository.latest_version_row(session, scenario["scenario_id"])
    return latest.version if latest is not None else scenario["version"]


def _write_commit(
    scenario_id: str,
    *,
    expected_version: int,
    confirm_token: str,
    resolved: list[dict[str, Any]],
    kind: str,
    option_id: str | None,
    idempotency_key: str,
    request_hash: str,
    actor: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    settings = load_settings()
    with session_scope() as session:
        replayed = _replay_commit(session, idempotency_key, request_hash)
        if replayed is not None:
            return _commit_response(replayed, replayed=True)

    scenario = repository.get_scenario(scenario_id)
    _assert_version(scenario, expected_version)
    verify_confirm_token(confirm_token, scenario_id, expected_version, resolved)
    _gate(scenario, resolved, kind)

    with session_scope() as session:
        replayed = _replay_commit(session, idempotency_key, request_hash)
        if replayed is not None:
            return _commit_response(replayed, replayed=True)
        current = _current_version(session, scenario)
        if current != expected_version:
            raise CommitError(
                f"stale expected_plan_version: got {expected_version}, current is "
                f"{current}; re-read the plan and try again"
            )
        if current == scenario["version"] and repository.latest_version_row(
            session, scenario_id
        ) is None:
            repository.materialize_seed(session, scenario)
        after = apply_change_set(scenario, resolved, kind=kind)
        after["version"] = current + 1
        try:
            repository.save_version(
                session, after, origin="commit", version=current + 1
            )
        except IntegrityError as exc:
            raise CommitError(
                "a concurrent commit advanced this plan; re-read it and retry"
            ) from exc
        writeback = settings.pm_writeback_enabled and settings.pm_provider != "none"
        row = RecoveryCommit(
            scenario_id=scenario_id,
            kind=kind,
            option_id=option_id,
            changes=canonical_json(resolved),
            diff=canonical_json(diff_scenario(scenario, after)),
            from_version=current,
            to_version=current + 1,
            status="writeback_pending" if writeback else "local_only",
            created_at=utcnow_iso(),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            actor=redact(actor) if actor else None,
        )
        session.add(row)
        session.flush()
        if writeback:
            session.add(
                Outbox(
                    commit_id=row.id,
                    provider=settings.pm_provider,
                    payload=canonical_json(
                        {
                            "scenario_id": scenario_id,
                            "commit_id": row.id,
                            "kind": kind,
                            "option_id": option_id,
                            "changes": resolved,
                            "from_version": current,
                            "to_version": current + 1,
                        }
                    ),
                    status="pending",
                    attempts=0,
                    created_at=utcnow_iso(),
                )
            )
        _audit(
            session,
            action=f"commit_{kind}",
            scenario_id=scenario_id,
            outcome="ok",
            detail={
                "commit_id": row.id,
                "option_id": option_id,
                "from_version": current,
                "to_version": current + 1,
                "changes": resolved,
            },
            actor=actor,
            request_id=request_id,
        )
        response = _commit_response(row, replayed=False)

    _after_commit(response, writeback=writeback)
    return response


def _after_commit(response: dict[str, Any], *, writeback: bool) -> None:
    """Fire-and-forget side effects after the transaction has committed."""
    try:
        from rippleback import notify

        notify.notify_event(
            "commit_recorded",
            {
                "scenario_id": response["scenario_id"],
                "commit_id": response["commit_id"],
                "kind": response["kind"],
                "to_version": response["to_version"],
                "status": response["status"],
            },
        )
    except Exception:  # pragma: no cover - notifications never block a commit
        pass
    if not writeback:
        return
    try:
        from rippleback.integrations import outbox

        outbox.kick()
    except Exception:  # pragma: no cover - worker start is best effort
        pass


def commit_changes(
    scenario_id: str,
    *,
    expected_version: Any,
    confirm_token: Any,
    changes: Any = None,
    option_id: Any = None,
    kind: str = KIND_RECOVERY,
    idempotency_key: Any = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Commit a confirmed recovery/reassignment with idempotent replay.

    Idempotency is keyed on the *raw request* (scenario, expected version,
    kind, option id, and the change set exactly as supplied), so a retry after
    a successful commit replays the stored result instead of re-resolving the
    now-modified plan.
    """
    _assert_writes_enabled()
    expected = _expected_version(expected_version)
    key = _require_nonempty(idempotency_key, "idempotency_key")
    request_hash = _request_hash(
        {
            "scenario_id": scenario_id,
            "expected_plan_version": expected,
            "kind": kind,
            "option_id": option_id,
            "changes": changes,
        }
    )
    with session_scope() as session:
        replayed = _replay_commit(session, key, request_hash)
        if replayed is not None:
            return _commit_response(replayed, replayed=True)
    scenario = repository.get_scenario(scenario_id)
    _assert_version(scenario, expected)
    resolved = resolve_changes(scenario, changes=changes, option_id=option_id, kind=kind)
    return _write_commit(
        scenario_id,
        expected_version=expected,
        confirm_token=_require_nonempty(confirm_token, "confirm_token"),
        resolved=resolved,
        kind=kind,
        option_id=option_id,
        idempotency_key=key,
        request_hash=request_hash,
        actor=actor,
        request_id=request_id,
    )


def commit_recovery(
    scenario_id: str,
    *,
    expected_version: Any,
    confirm_token: Any,
    changes: Any = None,
    option_id: Any = None,
    idempotency_key: Any = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return commit_changes(
        scenario_id,
        expected_version=expected_version,
        confirm_token=confirm_token,
        changes=changes,
        option_id=option_id,
        kind=KIND_RECOVERY,
        idempotency_key=idempotency_key,
        actor=actor,
        request_id=request_id,
    )


def commit_reassignment(
    scenario_id: str,
    *,
    expected_version: Any,
    confirm_token: Any,
    changes: Any,
    idempotency_key: Any = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return commit_changes(
        scenario_id,
        expected_version=expected_version,
        confirm_token=confirm_token,
        changes=changes,
        kind=KIND_REASSIGNMENT,
        idempotency_key=idempotency_key,
        actor=actor,
        request_id=request_id,
    )


# ── Disruptions ─────────────────────────────────────────────────────────────


def _disruption_response(row: Disruption, *, replayed: bool) -> dict[str, Any]:
    entity_status = row.prior_entity_status
    if row.status == "open" and row.prior_entity_status != "done":
        entity_status = "blocked"
    return {
        "scenario_id": row.scenario_id,
        "disruption_id": row.id,
        "entity_id": row.entity_id,
        "event_type": row.event_type,
        "severity": row.severity,
        "status": row.status,
        "plan_version": row.plan_version,
        "previous_entity_status": row.prior_entity_status,
        "entity_status": entity_status,
        "recorded_at": row.recorded_at,
        "resolved_at": row.resolved_at,
        "replayed": replayed,
    }


def record_disruption(
    scenario_id: str,
    *,
    entity_id: Any,
    event_type: Any = "service_outage",
    reason: Any = None,
    severity: Any = "high",
    delay_minutes: Any = None,
    idempotency_key: Any = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Record a real disruption: block the entity and advance the plan version.

    Documented rule: the plan status becomes ``disrupted`` and the entity
    becomes ``blocked`` unless it is already ``done`` — completed work is never
    re-blocked. The prior statuses are stored and restored on resolution.
    """
    _assert_writes_enabled()
    key = _require_nonempty(idempotency_key, "idempotency_key")
    entity = _require_nonempty(entity_id, "entity_id")
    event = _require_nonempty(event_type, "event_type")
    if event not in VALID_EVENT_TYPES:
        raise CommitError(
            f"unknown event_type {event!r}; allowed: {list(VALID_EVENT_TYPES)}"
        )
    if event in DELAY_EVENT_TYPES and delay_minutes is None:
        raise CommitError(f"event_type {event!r} requires delay_minutes")
    if delay_minutes is not None:
        if (
            not isinstance(delay_minutes, (int, float))
            or isinstance(delay_minutes, bool)
            or delay_minutes < 0
        ):
            raise CommitError("delay_minutes must be a non-negative number")
    severity = _require_nonempty(severity, "severity").lower()
    if severity not in VALID_SEVERITIES:
        raise CommitError(
            f"unknown severity {severity!r}; allowed: {list(VALID_SEVERITIES)}"
        )
    scenario = repository.get_scenario(scenario_id)
    if entity not in {item["id"] for item in scenario["entities"]}:
        raise CommitError(
            f"unknown entity_id {entity!r}; available: "
            f"{sorted(item['id'] for item in scenario['entities'])}"
        )
    request_hash = _request_hash(
        {
            "scenario_id": scenario_id,
            "entity_id": entity,
            "event_type": event,
            "reason": reason,
            "severity": severity,
            "delay_minutes": delay_minutes,
        }
    )
    with session_scope() as session:
        existing = session.execute(
            select(Disruption).where(Disruption.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise CommitError(
                    "idempotency_key was already used for a different request"
                )
            return _disruption_response(existing, replayed=True)
        current = _current_version(session, scenario)
        if repository.latest_version_row(session, scenario_id) is None:
            repository.materialize_seed(session, scenario)
        after = copy.deepcopy(scenario)
        target = next(item for item in after["entities"] if item["id"] == entity)
        prior_entity_status = target["status"]
        prior_plan_status = after["status"]
        if target["status"] != "done":
            target["status"] = "blocked"
        after["status"] = "disrupted"
        after["version"] = current + 1
        repository.save_version(session, after, origin="disruption", version=current + 1)
        row = Disruption(
            scenario_id=scenario_id,
            entity_id=entity,
            event_type=event,
            reason=redact(reason) if reason else None,
            severity=severity,
            delay_minutes=delay_minutes,
            prior_entity_status=prior_entity_status,
            prior_plan_status=prior_plan_status,
            status="open",
            recorded_at=utcnow_iso(),
            plan_version=current + 1,
            idempotency_key=key,
            request_hash=request_hash,
            actor=redact(actor) if actor else None,
        )
        session.add(row)
        session.flush()
        _audit(
            session,
            action="record_disruption",
            scenario_id=scenario_id,
            outcome="ok",
            detail={
                "disruption_id": row.id,
                "entity_id": entity,
                "event_type": event,
                "severity": severity,
                "delay_minutes": delay_minutes,
                "from_version": current,
                "to_version": current + 1,
            },
            actor=actor,
            request_id=request_id,
        )
        response = _disruption_response(row, replayed=False)

    try:
        from rippleback import notify

        notify.notify_event(
            "disruption_recorded",
            {
                "scenario_id": scenario_id,
                "entity_id": entity,
                "event_type": event,
                "severity": severity,
                "plan_version": response["plan_version"],
            },
        )
    except Exception:  # pragma: no cover
        pass
    return response


def resolve_disruption(
    disruption_id: Any,
    *,
    idempotency_key: Any = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Resolve an open disruption and restore the recorded prior statuses."""
    _assert_writes_enabled()
    try:
        target_id = int(disruption_id)
    except (TypeError, ValueError) as exc:
        raise CommitError("disruption_id must be an integer") from exc
    with session_scope() as session:
        row = session.get(Disruption, target_id)
        if row is None:
            raise CommitError(f"unknown disruption_id {target_id}")
        if row.status == "resolved":
            return _disruption_response(row, replayed=True)
        scenario = repository.get_scenario(row.scenario_id)
        current = _current_version(session, scenario)
        after = copy.deepcopy(scenario)
        if row.prior_entity_status is not None:
            for entity in after["entities"]:
                if entity["id"] == row.entity_id and entity["status"] == "blocked":
                    entity["status"] = row.prior_entity_status
        remaining = session.execute(
            select(Disruption.id).where(
                Disruption.scenario_id == row.scenario_id,
                Disruption.status == "open",
                Disruption.id != row.id,
            )
        ).scalars().all()
        if not remaining:
            after["status"] = row.prior_plan_status or "healthy"
        after["version"] = current + 1
        repository.save_version(session, after, origin="disruption", version=current + 1)
        row.status = "resolved"
        row.resolved_at = utcnow_iso()
        row.plan_version = current + 1
        _audit(
            session,
            action="resolve_disruption",
            scenario_id=row.scenario_id,
            outcome="ok",
            detail={
                "disruption_id": row.id,
                "entity_id": row.entity_id,
                "from_version": current,
                "to_version": current + 1,
            },
            actor=actor,
            request_id=request_id,
        )
        response = _disruption_response(row, replayed=False)
    try:
        from rippleback import notify

        notify.notify_event(
            "disruption_resolved",
            {
                "scenario_id": response["scenario_id"],
                "disruption_id": response["disruption_id"],
                "entity_id": response["entity_id"],
            },
        )
    except Exception:  # pragma: no cover
        pass
    return response


def ensure_writable_scenario(scenario_id: str) -> dict[str, Any]:
    """Load and validate a scenario for a write (raises ``CommitError`` on bad data)."""
    try:
        return repository.get_scenario(scenario_id)
    except ScenarioError as exc:
        raise CommitError(str(exc)) from exc
