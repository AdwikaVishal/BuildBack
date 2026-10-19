"""Persistence layer: engine, ORM models, and a tiny versioned migration runner.

Design notes
------------
* **SQLite by default**, with WAL journaling and a busy timeout so the MCP
  server, the adapter, and background workers can share one file. Any
  SQLAlchemy URL works (``RIPPLEBACK_DB_URL`` / ``DATABASE_URL``); Postgres needs
  no schema changes but is not required.
* **Snapshots are the source of truth.** Each committed state of a plan is a
  full ``plan_versions`` row (canonical JSON). There is no separate
  ``resources`` table: resources live inside the snapshot, exactly as the
  scenario schema already models them, so a version is self-contained and
  reproducible. This is recorded in ``docs/DECISIONS.md``.
* **No Alembic.** Schema evolution uses :data:`MIGRATIONS`, an ordered list of
  ``(version, apply)`` steps tracked in ``schema_migrations``. Version 1 is
  ``create_all``; later versions add columns/tables with plain SQL.
* Timestamps are ISO-8601 UTC strings; JSON payloads are canonical
  (sorted keys, no insignificant whitespace) so content hashes are stable.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from rippleback.config import DEFAULT_DATABASE_URL, load_settings


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(data: Any) -> str:
    """Deterministic JSON text: sorted keys, compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    """SHA-256 of :func:`canonical_json` — the plan-version identity."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class Base(DeclarativeBase):
    """Declarative base for every RippleBack table."""


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(64), nullable=False)


class AppSetting(Base):
    """Small key/value store for non-secret internal state (e.g. token secret)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class Plan(Base):
    """Per-plan metadata. The state of a plan lives in ``plan_versions``."""

    __tablename__ = "plans"

    scenario_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seeded")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release_entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class PlanVersion(Base):
    """One immutable committed state of a plan (canonical JSON snapshot)."""

    __tablename__ = "plan_versions"

    scenario_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("plans.scenario_id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class Disruption(Base):
    """A recorded (committed) disruption; open until explicitly resolved."""

    __tablename__ = "disruptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    delay_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_entity_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prior_plan_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    recorded_at: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RecoveryCommit(Base):
    """A committed recovery/reassignment, its overlay changes, and push state."""

    __tablename__ = "recovery_commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    option_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    changes: Mapped[str] = mapped_column(Text, nullable=False)
    diff: Mapped[str] = mapped_column(Text, nullable=False)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    pushed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Outbox(Base):
    """Queued PM write-back payloads, drained by an idempotent worker."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recovery_commits.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    """Append-only record of every mutating action (values already redacted)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SubstituteCatalogEntry(Base):
    """Explicit substitute declarations imported into a scenario."""

    __tablename__ = "substitute_catalog"

    scenario_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    substitute_entity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="import")
    added_at: Mapped[str] = mapped_column(String(64), nullable=False)


class IncidentHistory(Base):
    """Recorded incidents feeding the risk metric (PagerDuty or imported)."""

    __tablename__ = "incident_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    occurred_at: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="import")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_incident_source_external"),
    )


class SyncState(Base):
    """Last sync outcome per scenario, for the status endpoint and overlays."""

    __tablename__ = "sync_state"

    scenario_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


def _migration_1(engine: Engine) -> None:
    """Create the initial schema."""
    Base.metadata.create_all(engine)


MIGRATIONS: list[tuple[int, Callable[[Engine], None]]] = [
    (1, _migration_1),
]


_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, Engine] = {}


def _apply_sqlite_pragmas(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def get_engine(url: str | None = None) -> Engine:
    """Return a cached engine for ``url`` (defaults to the configured one)."""
    resolved = url or load_settings().database_url or DEFAULT_DATABASE_URL
    with _ENGINE_LOCK:
        engine = _ENGINES.get(resolved)
        if engine is None:
            kwargs: dict[str, Any] = {"future": True}
            if resolved.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
            engine = create_engine(resolved, **kwargs)
            _apply_sqlite_pragmas(engine)
            _ENGINES[resolved] = engine
        return engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Session factory bound to the (cached) engine for ``url``."""
    return sessionmaker(
        bind=get_engine(url), expire_on_commit=False, future=True, class_=Session
    )


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error, always close."""
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_migrations(engine: Engine) -> list[int]:
    """Apply every pending migration; return the versions applied this run."""
    applied: list[int] = []
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        try:
            rows = conn.execute(select(SchemaMigration.version)).scalars().all()
            done = set(rows)
        except Exception:
            done = set()
    for version, apply in MIGRATIONS:
        if version in done:
            continue
        apply(engine)
        with engine.begin() as conn:
            conn.execute(
                SchemaMigration.__table__.insert().values(
                    version=version, applied_at=utcnow_iso()
                )
            )
        applied.append(version)
    return applied


def init_db(url: str | None = None) -> Engine:
    """Create the engine, ensure the schema exists, and return it."""
    engine = get_engine(url)
    run_migrations(engine)
    return engine


def reset_engine_cache() -> None:
    """Drop cached engines (used by tests that point at a fresh database)."""
    with _ENGINE_LOCK:
        for engine in _ENGINES.values():
            engine.dispose()
        _ENGINES.clear()
