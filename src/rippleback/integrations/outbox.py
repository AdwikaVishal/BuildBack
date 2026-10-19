"""Outbox worker: drain queued PM write-back payloads.

Claims are taken by flipping rows to ``sending`` inside a transaction, so two
processes (server and adapter) can share one database without double-pushing.
Rows stuck in ``sending`` for over ten minutes are reclaimed (crash recovery).
Failures are retried up to ``max_attempts`` times, then the row and its commit
are marked failed and ``writeback_failed`` is notified once.

Everything here is best-effort from the caller's perspective: :func:`kick`
starts a background thread and returns immediately.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from sqlalchemy import select

from rippleback import repository
from rippleback.db import (
    AuditLog,
    Outbox,
    RecoveryCommit,
    session_scope,
    utcnow_iso,
)
from rippleback.security import redact

MAX_ATTEMPTS = 5
CLAIM_TIMEOUT_SECONDS = 600
KICK_LOCK = threading.Lock()
_KICKED = False


def _claim_batch(limit: int) -> list[int]:
    """Mark up to ``limit`` rows ``sending`` and return their ids."""
    stale_cutoff = time.time() - CLAIM_TIMEOUT_SECONDS
    with session_scope() as session:
        rows = session.execute(
            select(Outbox)
            .where(Outbox.status.in_(("pending", "sending")))
            .order_by(Outbox.id)
            .limit(limit)
        ).scalars().all()
        claimed: list[int] = []
        for row in rows:
            if row.status == "sending":
                try:
                    created = datetime_epoch(row.created_at)
                except Exception:
                    created = time.time()
                if created > stale_cutoff:
                    continue
            if row.status == "pending" and row.attempts >= MAX_ATTEMPTS:
                continue
            row.status = "sending"
            row.attempts = (row.attempts or 0) + 1
            claimed.append(row.id)
        return claimed


def datetime_epoch(iso_value: str) -> float:
    from datetime import datetime

    parsed = datetime.fromisoformat(iso_value)
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _finish_success(outbox_id: int, commit_id: int) -> None:
    with session_scope() as session:
        row = session.get(Outbox, outbox_id)
        if row is not None:
            row.status = "sent"
            row.sent_at = utcnow_iso()
            row.last_error = None
        commit = session.get(RecoveryCommit, commit_id)
        if commit is not None:
            commit.status = "pushed"
            commit.pushed_at = utcnow_iso()
            commit.last_error = None
        session.add(
            AuditLog(
                at=utcnow_iso(),
                actor="outbox-worker",
                action="push_writeback",
                scenario_id=commit.scenario_id if commit is not None else None,
                outcome="ok",
                detail=json.dumps({"outbox_id": outbox_id, "commit_id": commit_id}),
                request_id=None,
            )
        )


def _finish_failure(outbox_id: int, commit_id: int, error: str) -> None:
    with session_scope() as session:
        row = session.get(Outbox, outbox_id)
        commit = session.get(RecoveryCommit, commit_id)
        message = redact(error)[:2000]
        exhausted = row is not None and row.attempts >= MAX_ATTEMPTS
        if row is not None:
            row.status = "failed" if exhausted else "pending"
            row.last_error = message
        if commit is not None:
            commit.last_error = message
            commit.status = "writeback_failed" if exhausted else commit.status
        session.add(
            AuditLog(
                at=utcnow_iso(),
                actor="outbox-worker",
                action="push_writeback",
                scenario_id=commit.scenario_id if commit is not None else None,
                outcome="error",
                detail=json.dumps(
                    {
                        "outbox_id": outbox_id,
                        "commit_id": commit_id,
                        "error": message,
                        "exhausted": exhausted,
                    }
                ),
                request_id=None,
            )
        )
    if exhausted:
        _notify_failure(commit_id, message)


def _notify_failure(commit_id: int, message: str) -> None:
    try:
        from rippleback import notify

        notify.notify_event(
            "writeback_failed", {"commit_id": commit_id, "error": message}
        )
    except Exception:  # pragma: no cover
        pass


def process_outbox(limit: int = 20) -> dict[str, int]:
    """Drain up to ``limit`` outbox rows; returns ``{sent, failed}`` counts."""
    from rippleback.integrations import get_provider

    claimed = _claim_batch(limit)
    sent = 0
    failed = 0
    for outbox_id in claimed:
        with session_scope() as session:
            row = session.get(Outbox, outbox_id)
            if row is None:
                continue
            payload = json.loads(row.payload)
            commit_id = row.commit_id
            provider_name = row.provider
        try:
            scenario = repository.get_scenario(payload["scenario_id"])
            provider = get_provider(provider_name)
            try:
                provider.push_changes(
                    scenario, payload["changes"], payload.get("kind", "recovery")
                )
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            _finish_success(outbox_id, commit_id)
            sent += 1
        except Exception as exc:  # transport/provider failure — recorded, retried
            _finish_failure(outbox_id, commit_id, str(exc))
            failed += 1
    return {"sent": sent, "failed": failed}


def retry_commit(commit_id: int) -> dict[str, Any]:
    """Re-queue a commit's failed/unsent outbox row and kick the worker.

    Returns ``{"requeued": bool, "commit_id": ..., "status": ...}``; a commit
    without an outbox row (writes disabled at commit time, or already pushed)
    requeues nothing.
    """
    with session_scope() as session:
        commit = session.get(RecoveryCommit, commit_id)
        if commit is None:
            return {"requeued": False, "commit_id": commit_id, "status": "unknown"}
        row = session.execute(
            select(Outbox).where(Outbox.commit_id == commit_id).order_by(Outbox.id.desc())
        ).scalars().first()
        if row is None or row.status == "sent":
            return {"requeued": False, "commit_id": commit_id, "status": commit.status}
        row.status = "pending"
        row.attempts = 0
        row.last_error = None
        if commit.status in ("local_only", "writeback_failed"):
            commit.status = "writeback_pending"
        status = commit.status
    kick()
    return {"requeued": True, "commit_id": commit_id, "status": status}


def kick() -> threading.Thread | None:
    """Start a one-shot background drain (at most one outstanding thread)."""
    global _KICKED
    with KICK_LOCK:
        if _KICKED:
            return None

        def _run() -> None:
            global _KICKED
            try:
                process_outbox()
            except Exception:  # pragma: no cover - background safety net
                pass
            finally:
                with KICK_LOCK:
                    _KICKED = False

        thread = threading.Thread(target=_run, name="rippleback-outbox", daemon=True)
        _KICKED = True
        thread.start()
        return thread
