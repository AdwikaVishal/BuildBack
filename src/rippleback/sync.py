"""PM → RippleBack sync with local-commit overlay re-application.

One sync pass:

1. fetch the plan from the configured provider and build a validated scenario;
2. retire any overlay commit whose effect the imported data already contains
   (``is_reflected``), marking it ``synced_back``;
3. re-apply every still-pending overlay commit on top of the imported data, so a
   locally committed recovery is **never erased** by a sync before the PM
   reflects it;
4. store the result as the next plan version **only when its content hash
   changed** (idempotent re-syncs do not advance versions);
5. upsert ``sync_state`` and write an audit row.

Failures are recorded (``sync_state.last_status = "error"``, redacted message,
``sync_failed`` notification) and returned as a structured result — never
raised at the HTTP boundary as a bare 500.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any

from sqlalchemy import select

from rippleback import repository
from rippleback.commit import is_reflected
from rippleback.config import _env, load_settings
from rippleback.db import (
    AuditLog,
    RecoveryCommit,
    SyncState,
    content_hash,
    session_scope,
    utcnow_iso,
)
from rippleback.integrations import IntegrationError, build_scenario, get_provider
from rippleback.repository import OVERLAY_STATUSES, SYNCED_BACK_STATUS
from rippleback.scenarios import validate_scenario
from rippleback.security import redact


def sync_plan(
    scenario_id: str | None = None,
    *,
    provider_name: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Sync one plan from the PM provider; returns a structured result."""
    provider = None
    provider_label = provider_name or load_settings().pm_provider
    try:
        provider = get_provider(provider_name, client=client)
        provider.require_configured()
        plan = provider.fetch_plan()
        scenario = build_scenario(plan)
        errors = validate_scenario(scenario)
        if errors:
            raise IntegrationError(
                f"{provider.name} returned a plan that fails schema validation: "
                f"{'; '.join(errors)}"
            )
        if scenario_id and scenario_id != plan.scenario_id:
            raise IntegrationError(
                f"requested scenario_id {scenario_id!r} does not match the "
                f"configured provider plan {plan.scenario_id!r}"
            )
        sid = plan.scenario_id
        with session_scope() as session:
            latest = repository.latest_version_row(session, sid)
            current = latest.version if latest is not None else 1
            retired = 0
            commits = session.execute(
                select(RecoveryCommit)
                .where(
                    RecoveryCommit.scenario_id == sid,
                    RecoveryCommit.status.in_(OVERLAY_STATUSES),
                )
                .order_by(RecoveryCommit.id)
            ).scalars().all()
            for commit in commits:
                if is_reflected(
                    scenario, json.loads(commit.changes), kind=commit.kind
                ):
                    commit.status = SYNCED_BACK_STATUS
                    retired += 1
            imported = copy.deepcopy(scenario)
            imported["version"] = current
            overlaid = repository.apply_overlays(session, imported)
            overlay_errors = validate_scenario(overlaid)
            if overlay_errors:
                raise IntegrationError(
                    "overlay re-application produced an invalid plan: "
                    f"{'; '.join(overlay_errors)}"
                )
            overlaid_hash = content_hash(overlaid)
            if latest is not None and overlaid_hash == latest.content_hash:
                status = "unchanged"
                stored_version = latest.version
            else:
                stored_version = current + 1 if latest is not None else current
                repository.save_version(
                    session,
                    overlaid,
                    origin="sync",
                    version=stored_version,
                    source=provider.name,
                    external_id=plan.external_id,
                )
                status = "ok"
            state = session.get(SyncState, sid)
            if state is None:
                state = SyncState(scenario_id=sid, provider=provider.name)
                session.add(state)
            state.provider = provider.name
            state.external_id = plan.external_id
            state.last_synced_at = utcnow_iso()
            state.last_status = status
            state.last_error = None
            state.snapshot_hash = overlaid_hash
            state.updated_at = utcnow_iso()
            session.add(
                AuditLog(
                    at=utcnow_iso(),
                    actor="sync",
                    action="sync_plan",
                    scenario_id=sid,
                    outcome="ok",
                    detail=json.dumps(
                        {
                            "provider": provider.name,
                            "status": status,
                            "version": stored_version,
                            "entities": len(overlaid["entities"]),
                            "dependencies": len(overlaid["dependencies"]),
                            "retired_overlays": retired,
                            "out_of_scope": plan.out_of_scope,
                        }
                    ),
                    request_id=None,
                )
            )
        return {
            "ok": True,
            "scenario_id": sid,
            "provider": provider.name,
            "status": status,
            "version": stored_version,
            "entities": len(overlaid["entities"]),
            "dependencies": len(overlaid["dependencies"]),
            "retired_overlays": retired,
            "out_of_scope": plan.out_of_scope,
        }
    except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
        message = redact(str(exc))
        missing = getattr(exc, "missing", [])
        _record_failure(scenario_id, provider_label, message)
        return {
            "ok": False,
            "scenario_id": scenario_id,
            "provider": provider_label,
            "error": message,
            "missing": missing,
        }
    finally:
        if provider is not None:
            close = getattr(provider, "close", None)
            if callable(close):
                close()


def _record_failure(scenario_id: str | None, provider: str, message: str) -> None:
    try:
        with session_scope() as session:
            if scenario_id:
                state = session.get(SyncState, scenario_id)
                if state is None:
                    state = SyncState(scenario_id=scenario_id, provider=provider)
                    session.add(state)
                state.provider = provider
                state.last_status = "error"
                state.last_error = message
                state.updated_at = utcnow_iso()
            session.add(
                AuditLog(
                    at=utcnow_iso(),
                    actor="sync",
                    action="sync_plan",
                    scenario_id=scenario_id,
                    outcome="error",
                    detail=json.dumps({"provider": provider, "error": message}),
                    request_id=None,
                )
            )
    except Exception:  # pragma: no cover - recording must never mask
        pass
    try:
        from rippleback import notify

        notify.notify_event(
            "sync_failed",
            {"scenario_id": scenario_id, "provider": provider, "error": message},
        )
    except Exception:  # pragma: no cover
        pass


def sync_all(*, client: Any = None) -> dict[str, Any]:
    """Sync the configured plan (if any) and ingest incidents (if configured)."""
    settings = load_settings()
    result: dict[str, Any] = {"ok": False, "plan": None, "incidents": None}
    if settings.pm_provider == "none":
        result["plan"] = {
            "ok": False,
            "error": "no PM provider configured",
            "missing": ["RIPPLEBACK_PM_PROVIDER"],
        }
    else:
        result["plan"] = sync_plan(client=client)
        result["ok"] = bool(result["plan"].get("ok")) or result["ok"]
    if _env("PAGERDUTY_API_KEY"):
        try:
            from rippleback.integrations import pagerduty_ingest

            result["incidents"] = pagerduty_ingest(client=client)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["incidents"] = {"ok": False, "error": redact(str(exc))}
    return result


def start_sync_loop(stop: threading.Event) -> threading.Thread | None:
    """Start the periodic sync + outbox drain loop (no-op when unconfigured)."""
    settings = load_settings()
    if settings.pm_provider == "none" or settings.sync_interval_seconds <= 0:
        return None

    def _run() -> None:
        while not stop.wait(settings.sync_interval_seconds):
            try:
                sync_all()
            except Exception:  # pragma: no cover - loop safety net
                pass
            try:
                from rippleback.integrations import outbox

                outbox.process_outbox()
            except Exception:  # pragma: no cover
                pass

    thread = threading.Thread(target=_run, name="rippleback-sync", daemon=True)
    thread.start()
    return thread
