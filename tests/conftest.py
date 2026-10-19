"""Shared pytest configuration.

Every test runs against a throwaway SQLite database:

* ``RIPPLEBACK_DB_URL`` points at a session-scoped temp file, so nothing a test
  commits can leak into (or be broken by) a developer's real ``./rippleback.db``;
* writes, PM provider, API key, token secret, and the sync interval are forced
  to safe defaults so a developer's ``.env`` can never change test behavior;
* the engine cache is reset around the run, and subprocess-based test fixtures
  inherit the same environment.

Individual tests enable writes with ``monkeypatch.setenv(
"RIPPLEBACK_WRITES_ENABLED", "true")``.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_database(tmp_path_factory: pytest.TempPathFactory):
    db_path = tmp_path_factory.mktemp("rippleback") / "test.db"
    os.environ["RIPPLEBACK_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ.pop("DATABASE_URL", None)
    os.environ["RIPPLEBACK_WRITES_ENABLED"] = "false"
    os.environ["RIPPLEBACK_PM_PROVIDER"] = "none"
    os.environ.pop("RIPPLEBACK_API_KEY", None)
    os.environ.pop("RIPPLEBACK_TOKEN_SECRET", None)
    os.environ["RIPPLEBACK_SYNC_INTERVAL_SECONDS"] = "0"
    os.environ["RIPPLEBACK_PM_WRITEBACK_ENABLED"] = "false"

    from rippleback.db import init_db, reset_engine_cache

    reset_engine_cache()
    init_db()
    yield
    reset_engine_cache()


@pytest.fixture
def anyio_backend() -> str:
    """Run anyio-marked tests on asyncio only (no trio dependency)."""
    return "asyncio"
