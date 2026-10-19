"""Plan repository: the one place scenarios are loaded from.

Lookup order for :func:`get_scenario`:

1. The in-process ``SCENARIOS`` dict (seeded demo scenarios — kept first-class
   so tests can monkeypatch it). Its data is validated exactly as before.
2. The ``plan_versions`` table, when a plan has been materialized (synced from a
   PM provider or committed against the seed).

For a seeded id, once a ``plans`` row exists the newest DB snapshot wins,
because it is that plan's later committed state (it always includes the seed as
version 1). A DB-only id is served straight from its newest snapshot. Both
paths run the same :func:`rippleback.scenarios.validate_scenario` gate.

On top of the loaded snapshot the repository:

* re-applies overlay commits (locally committed recoveries whose PM write-back
  has not yet been observed in imported data), and
* injects explicit substitute candidates from the ``substitute_catalog`` table
  into each entity's ``metadata.substitutes``.

Injection only touches scenarios that actually have catalog rows, so pristine
seeds — and every test running against an empty database — are returned
byte-for-byte identical.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rippleback import scenarios as scenarios_module
from rippleback.db import (
    AuditLog,
    Disruption,
    IncidentHistory,
    Outbox,
    Plan,
    PlanVersion,
    RecoveryCommit,
    SubstituteCatalogEntry,
    SyncState,
    canonical_json,
    content_hash,
    session_scope,
    utcnow_iso,
)
from rippleback.scenarios import ScenarioError, validate_scenario
from rippleback.security import redact, redact_mapping

# Commit statuses whose changes stay overlaid on imported data until the PM
# provider's data is observed to already contain them.
OVERLAY_STATUSES = (
    "local_only",
    "writeback_pending",
    "writeback_failed",
    "pushed",
)
# Once a push is observed back in imported data the overlay entry is retired.
SYNCED_BACK_STATUS = "synced_back"

OPEN = "open"
RESOLVED = "resolved"


def _available_ids(session: Session) -> list[str]:
    ids = set(scenarios_module.SCENARIOS)
    ids.update(session.execute(select(Plan.scenario_id)).scalars().all())
    return sorted(ids)


def _validated(data: Any, label: str) -> dict[str, Any]:
    errors = validate_scenario(data)
    if errors:
        raise ScenarioError(f"malformed scenario {label!r}: {'; '.join(errors)}")
    return data


def latest_version_row(session: Session, scenario_id: str) -> PlanVersion | None:
    """Newest ``plan_versions`` row for a scenario, or ``None``."""
    return session.execute(
        select(PlanVersion)
        .where(PlanVersion.scenario_id == scenario_id)
        .order_by(PlanVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_snapshot(session: Session, scenario_id: str) -> dict[str, Any] | None:
    row = latest_version_row(session, scenario_id)
    if row is None:
        return None
    import json

    return json.loads(row.snapshot)


def materialize_seed(session: Session, scenario: dict[str, Any], *, source: str = "seeded") -> Plan:
    """Ensure a plan + version-1 snapshot exist for a seeded scenario.

    Idempotent. Returns the ``Plan`` row.
    """
    scenario_id = scenario["scenario_id"]
    plan = session.get(Plan, scenario_id)
    now = utcnow_iso()
    if plan is None:
        plan = Plan(
            scenario_id=scenario_id,
            plan_id=scenario["plan_id"],
            source=source,
            external_id=None,
            release_entity_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
    if latest_version_row(session, scenario_id) is None:
        session.add(
            PlanVersion(
                scenario_id=scenario_id,
                version=scenario["version"],
                snapshot=canonical_json(scenario),
                content_hash=content_hash(scenario),
                origin="seed",
                created_at=now,
            )
        )
    session.flush()
    return plan


def save_version(
    session: Session,
    scenario: dict[str, Any],
    *,
    origin: str,
    version: int | None = None,
    source: str | None = None,
    external_id: str | None = None,
) -> PlanVersion:
    """Persist a full snapshot as the next (or given) plan version."""
    scenario_id = scenario["scenario_id"]
    plan = session.get(Plan, scenario_id)
    now = utcnow_iso()
    if plan is None:
        plan = Plan(
            scenario_id=scenario_id,
            plan_id=scenario["plan_id"],
            source=source or origin,
            external_id=external_id,
            release_entity_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(plan)
    else:
        plan.updated_at = now
        if source:
            plan.source = source
        if external_id is not None:
            plan.external_id = external_id
    latest = latest_version_row(session, scenario_id)
    target_version = version if version is not None else (
        (latest.version + 1) if latest else scenario["version"]
    )
    scenario = copy.deepcopy(scenario)
    scenario["version"] = target_version
    row = PlanVersion(
        scenario_id=scenario_id,
        version=target_version,
        snapshot=canonical_json(scenario),
        content_hash=content_hash(scenario),
        origin=origin,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def inject_substitutes(session: Session, scenario: dict[str, Any]) -> dict[str, Any]:
    """Merge ``substitute_catalog`` rows into entity ``metadata.substitutes``."""
    rows = session.execute(
        select(SubstituteCatalogEntry)
        .where(SubstituteCatalogEntry.scenario_id == scenario["scenario_id"])
        .order_by(
            SubstituteCatalogEntry.entity_id,
            SubstituteCatalogEntry.added_at,
            SubstituteCatalogEntry.substitute_entity_id,
        )
    ).scalars().all()
    if not rows:
        return scenario
    known = {entity["id"] for entity in scenario["entities"]}
    by_entity: dict[str, list[str]] = {}
    for row in rows:
        if row.entity_id not in known or row.substitute_entity_id not in known:
            continue
        by_entity.setdefault(row.entity_id, []).append(row.substitute_entity_id)
    for entity in scenario["entities"]:
        candidates = by_entity.get(entity["id"])
        if not candidates:
            continue
        metadata = dict(entity.get("metadata") or {})
        existing = metadata.get("substitutes")
        if isinstance(existing, list):
            merged = [str(item) for item in existing]
            merged.extend(c for c in candidates if c not in merged)
            metadata["substitutes"] = merged
        else:
            metadata["substitutes"] = candidates
        entity["metadata"] = metadata
    return scenario


def apply_overlays(session: Session, scenario: dict[str, Any]) -> dict[str, Any]:
    """Re-apply not-yet-synced-back commits on top of an imported snapshot."""
    import json

    commits = session.execute(
        select(RecoveryCommit)
        .where(
            RecoveryCommit.scenario_id == scenario["scenario_id"],
            RecoveryCommit.status.in_(OVERLAY_STATUSES),
        )
        .order_by(RecoveryCommit.id)
    ).scalars().all()
    if not commits:
        return scenario
    from rippleback.commit import apply_change_set

    for commit in commits:
        changes = json.loads(commit.changes)
        scenario = apply_change_set(scenario, changes, kind=commit.kind)
    return scenario


def get_scenario(scenario_id: str) -> dict[str, Any]:
    """Load, validate, overlay, and substitute-annotate a scenario.

    Raises:
        ScenarioError: empty/unknown id, or malformed data at either source.
    """
    if not scenario_id or not scenario_id.strip():
        raise ScenarioError("scenario_id must be a non-empty string")
    key = scenario_id.strip()
    seeded = scenarios_module.SCENARIOS.get(key)
    with session_scope() as session:
        plan = session.get(Plan, key)
        snapshot = load_snapshot(session, key) if plan is not None else None
        if seeded is not None and snapshot is None:
            result = _validated(copy.deepcopy(seeded), key)
        elif snapshot is not None:
            result = _validated(snapshot, key)
        elif seeded is not None:
            result = _validated(copy.deepcopy(seeded), key)
        else:
            available = _available_ids(session)
            raise ScenarioError(
                f"unknown scenario_id {key!r}; available: {available}"
            )
        result = apply_overlays(session, result)
        result = inject_substitutes(session, result)
        return result


def list_scenarios() -> dict[str, Any]:
    """List every known scenario (seeded and DB-backed) with light metadata."""
    with session_scope() as session:
        entries: list[dict[str, Any]] = []
        for key in _available_ids(session):
            seeded = scenarios_module.SCENARIOS.get(key)
            plan = session.get(Plan, key)
            latest = latest_version_row(session, key)
            sync = session.get(SyncState, key)
            source = plan.source if plan is not None else "seeded"
            if plan is None and seeded is not None:
                version = seeded["version"]
                status = seeded["status"]
                plan_id = seeded["plan_id"]
                entity_count = len(seeded["entities"])
            elif latest is not None:
                import json

                snapshot = json.loads(latest.snapshot)
                version = latest.version
                status = snapshot.get("status", "unknown")
                plan_id = snapshot.get("plan_id", "")
                entity_count = len(snapshot.get("entities", []))
            else:
                continue
            open_count = len(
                session.execute(
                    select(Disruption.id).where(
                        Disruption.scenario_id == key,
                        Disruption.status == OPEN,
                    )
                ).scalars().all()
            )
            entries.append(
                {
                    "scenario_id": key,
                    "plan_id": plan_id,
                    "source": source,
                    "version": version,
                    "status": status,
                    "entity_count": entity_count,
                    "open_disruptions": open_count,
                    "last_synced_at": sync.last_synced_at if sync is not None else None,
                    "last_sync_status": sync.last_status if sync is not None else None,
                }
            )
        return {"scenarios": entries, "count": len(entries)}


def list_open_disruptions(scenario_id: str) -> list[dict[str, Any]]:
    """Open disruptions for a scenario, oldest first."""
    with session_scope() as session:
        rows = session.execute(
            select(Disruption)
            .where(Disruption.scenario_id == scenario_id, Disruption.status == OPEN)
            .order_by(Disruption.id)
        ).scalars().all()
        return [
            {
                "id": row.id,
                "entity_id": row.entity_id,
                "event_type": row.event_type,
                "reason": row.reason,
                "severity": row.severity,
                "delay_minutes": row.delay_minutes,
                "status": row.status,
                "recorded_at": row.recorded_at,
                "plan_version": row.plan_version,
            }
            for row in rows
        ]


def get_plan_meta(scenario_id: str) -> dict[str, Any]:
    """Plan/sync metadata for a scenario (used by status and sync code)."""
    with session_scope() as session:
        plan = session.get(Plan, scenario_id)
        sync = session.get(SyncState, scenario_id)
        return {
            "source": plan.source if plan is not None else "seeded",
            "external_id": plan.external_id if plan is not None else None,
            "release_entity_id": plan.release_entity_id if plan is not None else None,
            "last_synced_at": sync.last_synced_at if sync is not None else None,
            "last_sync_status": sync.last_status if sync is not None else None,
            "provider": sync.provider if sync is not None else None,
        }


def replace_substitute_catalog(
    scenario_id: str, declarations: dict[str, list[str]], *, source: str = "import"
) -> int:
    """Replace a scenario's catalog with ``declarations``; return rows written.

    Unknown entity references raise ``ScenarioError`` — nothing is inferred or
    silently dropped.
    """
    scenario = get_scenario(scenario_id)
    known = {entity["id"] for entity in scenario["entities"]}
    now = utcnow_iso()
    written = 0
    with session_scope() as session:
        for entity_id in list(declarations):
            if entity_id not in known:
                raise ScenarioError(
                    f"unknown entity_id {entity_id!r} for scenario {scenario_id!r}"
                )
            for substitute_id in declarations[entity_id]:
                if substitute_id not in known:
                    raise ScenarioError(
                        f"unknown substitute entity {substitute_id!r} referenced by "
                        f"{entity_id!r} in scenario {scenario_id!r}"
                    )
        session.query(SubstituteCatalogEntry).filter(
            SubstituteCatalogEntry.scenario_id == scenario_id
        ).delete()
        for entity_id, substitutes in declarations.items():
            for substitute_id in dict.fromkeys(substitutes):
                session.add(
                    SubstituteCatalogEntry(
                        scenario_id=scenario_id,
                        entity_id=entity_id,
                        substitute_entity_id=substitute_id,
                        source=source,
                        added_at=now,
                    )
                )
                written += 1
    return written


def record_incidents(rows: list[dict[str, Any]]) -> int:
    """Insert incident-history rows, de-duplicating on (source, external_id)."""
    written = 0
    with session_scope() as session:
        known = {
            (row.source, row.external_id)
            for row in session.execute(
                select(IncidentHistory).where(
                    IncidentHistory.external_id.is_not(None)
                )
            ).scalars().all()
        }
        for row in rows:
            key = (row.get("source", "import"), row.get("external_id"))
            if key in known and key[1] is not None:
                continue
            session.add(
                IncidentHistory(
                    scenario_id=row["scenario_id"],
                    entity_id=row.get("entity_id"),
                    severity=row.get("severity", "unknown"),
                    occurred_at=row["occurred_at"],
                    source=row.get("source", "import"),
                    external_id=row.get("external_id"),
                    summary=row.get("summary"),
                )
            )
            known.add(key)
            written += 1
    return written


def _commit_dict(row: RecoveryCommit) -> dict[str, Any]:
    import json

    return {
        "id": row.id,
        "scenario_id": row.scenario_id,
        "kind": row.kind,
        "option_id": row.option_id,
        "changes": json.loads(row.changes),
        "diff": json.loads(row.diff),
        "from_version": row.from_version,
        "to_version": row.to_version,
        "status": row.status,
        "created_at": row.created_at,
        "pushed_at": row.pushed_at,
        "last_error": redact(row.last_error) if row.last_error else None,
        "actor": row.actor,
    }


def list_commits(
    *, scenario_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent commit rows, newest first (optionally filtered by scenario)."""
    bounded = max(1, min(int(limit), 500))
    with session_scope() as session:
        query = select(RecoveryCommit).order_by(RecoveryCommit.id.desc()).limit(bounded)
        if scenario_id is not None:
            query = query.where(RecoveryCommit.scenario_id == scenario_id)
        return [_commit_dict(row) for row in session.execute(query).scalars().all()]


def get_commit(commit_id: int) -> dict[str, Any] | None:
    """One commit row (with its outbox state) or ``None``."""
    with session_scope() as session:
        row = session.get(RecoveryCommit, commit_id)
        if row is None:
            return None
        payload = _commit_dict(row)
        outbox = session.execute(
            select(Outbox).where(Outbox.commit_id == commit_id).order_by(Outbox.id.desc())
        ).scalars().first()
        payload["outbox"] = (
            {
                "id": outbox.id,
                "provider": outbox.provider,
                "status": outbox.status,
                "attempts": outbox.attempts,
                "last_error": redact(outbox.last_error) if outbox.last_error else None,
                "sent_at": outbox.sent_at,
            }
            if outbox is not None
            else None
        )
        return payload


def list_audit(
    *, scenario_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent audit rows, newest first (redacted at write time already)."""
    import json

    bounded = max(1, min(int(limit), 500))
    with session_scope() as session:
        query = select(AuditLog).order_by(AuditLog.id.desc()).limit(bounded)
        if scenario_id is not None:
            query = query.where(AuditLog.scenario_id == scenario_id)
        rows = session.execute(query).scalars().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            detail: Any = None
            if row.detail:
                try:
                    detail = json.loads(row.detail)
                except ValueError:
                    detail = redact(row.detail)
            result.append(
                {
                    "id": row.id,
                    "at": row.at,
                    "actor": row.actor,
                    "action": row.action,
                    "scenario_id": row.scenario_id,
                    "outcome": row.outcome,
                    "detail": redact_mapping(detail),
                    "request_id": row.request_id,
                }
            )
        return result
