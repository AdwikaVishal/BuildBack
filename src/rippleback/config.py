"""Environment-driven configuration for RippleBack (backend only).

Every knob is an environment variable. Nothing here is secret by default; the
only secret-bearing values (API tokens, AWS credentials, SMTP passwords) are
read from the environment and are **never** logged, returned over HTTP, or
written to the audit log — use :func:`rippleback.security.redact` before
interpolating any settings value into a message.

With an empty environment the application runs on SQLite with the seeded demo
scenarios, writes disabled, and every integration reported as not configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

try:  # Optional: an empty environment (or no python-dotenv) simply means env vars only.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass

DEFAULT_DATABASE_URL = "sqlite:///./rippleback.db"
DEFAULT_CONFIRM_TOKEN_TTL_SECONDS = 300
DEFAULT_SYNC_INTERVAL_SECONDS = 300
DEFAULT_CAPACITY = 3

# Environment variables whose values must never appear in logs, HTTP replies,
# audit rows, or error messages.
SECRET_ENV_VARS = (
    "RIPPLEBACK_API_KEY",
    "RIPPLEBACK_TOKEN_SECRET",
    "JIRA_API_TOKEN",
    "LINEAR_API_KEY",
    "GITHUB_TOKEN",
    "PAGERDUTY_API_KEY",
    "SLACK_WEBHOOK_URL",
    "SMTP_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float | None) -> float | None:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = _env(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    """Resolved settings snapshot (re-read from the environment each call)."""

    database_url: str = DEFAULT_DATABASE_URL
    writes_enabled: bool = False
    api_key: str = ""
    token_secret: str = ""
    confirm_token_ttl_seconds: int = DEFAULT_CONFIRM_TOKEN_TTL_SECONDS
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    adapter_host: str = "127.0.0.1"
    # PM provider
    pm_provider: str = "none"
    pm_writeback_enabled: bool = False
    sync_interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS
    out_of_scope_links: str = "stub"
    default_capacity: int = DEFAULT_CAPACITY
    resources_file: str = ""
    release_entity: str = ""
    # Scoring weights: None = auto (enabled only when the source is configured
    # and every option has a value); a float forces that weight.
    score_weight_delay: float | None = None
    score_weight_cost: float | None = None
    score_weight_risk: float | None = None
    score_weight_strain: float | None = None
    score_weight_disruption: float | None = None
    cost_rate_per_work_unit: float | None = None
    cost_per_change: float = 0.0
    substitutes_file: str = ""
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def writes_active(self) -> bool:
        return self.writes_enabled

    @property
    def api_key_required(self) -> bool:
        return bool(self.api_key)

    @property
    def adapter_bound_public(self) -> bool:
        return self.adapter_host not in ("127.0.0.1", "localhost", "::1")


def load_settings() -> Settings:
    """Read every setting from the environment (cheap; call per request)."""
    provider = _env("RIPPLEBACK_PM_PROVIDER", "none").lower()
    if provider not in {"none", "jira", "linear", "github"}:
        provider = "none"
    origins = _env_list("RIPPLEBACK_CORS_ORIGINS") or [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    return Settings(
        database_url=_env("RIPPLEBACK_DB_URL") or _env("DATABASE_URL") or DEFAULT_DATABASE_URL,
        writes_enabled=_env_bool("RIPPLEBACK_WRITES_ENABLED"),
        api_key=_env("RIPPLEBACK_API_KEY"),
        token_secret=_env("RIPPLEBACK_TOKEN_SECRET"),
        confirm_token_ttl_seconds=_env_int(
            "RIPPLEBACK_CONFIRM_TOKEN_TTL_SECONDS", DEFAULT_CONFIRM_TOKEN_TTL_SECONDS
        ),
        cors_origins=tuple(origins),
        adapter_host=_env("RIPPLEBACK_ADAPTER_HOST", "127.0.0.1"),
        pm_provider=provider,
        pm_writeback_enabled=_env_bool("RIPPLEBACK_PM_WRITEBACK_ENABLED"),
        sync_interval_seconds=_env_int(
            "RIPPLEBACK_SYNC_INTERVAL_SECONDS", DEFAULT_SYNC_INTERVAL_SECONDS
        ),
        out_of_scope_links=_env("RIPPLEBACK_OUT_OF_SCOPE_LINKS", "stub").lower()
        or "stub",
        default_capacity=_env_int("RIPPLEBACK_DEFAULT_CAPACITY", DEFAULT_CAPACITY),
        resources_file=_env("RESOURCES_FILE"),
        release_entity=_env("RIPPLEBACK_RELEASE_ENTITY"),
        score_weight_delay=_env_float("SCORE_WEIGHT_DELAY", None),
        score_weight_cost=_env_float("SCORE_WEIGHT_COST", None),
        score_weight_risk=_env_float("SCORE_WEIGHT_RISK", None),
        score_weight_strain=_env_float("SCORE_WEIGHT_STRAIN", None),
        score_weight_disruption=_env_float("SCORE_WEIGHT_DISRUPTION", None),
        cost_rate_per_work_unit=_env_float("COST_RATE_PER_WORK_UNIT", None),
        cost_per_change=_env_float("COST_PER_CHANGE", 0.0) or 0.0,
        substitutes_file=_env("SUBSTITUTES_FILE"),
    )


def integration_status() -> dict[str, Any]:
    """Report which integrations are configured (names only — never values).

    Used by ``GET /api/status`` and ``rippleback-doctor``. A missing
    integration reports the exact variable names required to enable it.
    """
    settings = load_settings()
    status: dict[str, Any] = {
        "database": {
            "configured": True,
            "url_scheme": settings.database_url.split("://", 1)[0]
            if "://" in settings.database_url
            else "sqlite",
        },
        "writes": {
            "enabled": settings.writes_enabled,
            "api_key_required": settings.api_key_required,
            "warn": (
                "adapter will bind a non-loopback host without RIPPLEBACK_API_KEY; "
                "writes will be refused"
                if settings.adapter_bound_public and not settings.api_key_required
                else None
            ),
        },
        "pm_provider": {"name": settings.pm_provider, "configured": False, "missing": []},
        "assistant": {
            "provider": _env("RIPPLEBACK_ASSISTANT_PROVIDER", "deterministic"),
            "bedrock_configured": bool(
                _env("RIPPLEBACK_BEDROCK_REGION") and _env("RIPPLEBACK_BEDROCK_MODEL_ID")
            ),
            "missing": [
                name
                for name in ("RIPPLEBACK_BEDROCK_REGION", "RIPPLEBACK_BEDROCK_MODEL_ID")
                if not _env(name)
            ]
            if _env("RIPPLEBACK_ASSISTANT_PROVIDER", "deterministic") == "bedrock"
            else [],
        },
        "notifications": {
            "slack": bool(_env("SLACK_WEBHOOK_URL")),
            "smtp": bool(_env("SMTP_HOST")),
            "missing": [
                name
                for name in ("SLACK_WEBHOOK_URL", "SMTP_HOST")
                if not _env(name)
            ],
        },
        "scoring": {
            "cost_source": settings.cost_rate_per_work_unit is not None
            or bool(_env("JIRA_FIELD_COST")),
            "risk_source": bool(_env("PAGERDUTY_API_KEY"))
            or bool(_env("JIRA_FIELD_RISK")),
        },
        "substitutes_file": bool(settings.substitutes_file),
    }
    provider_missing: dict[str, list[str]] = {
        "jira": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"],
        "linear": ["LINEAR_API_KEY", "LINEAR_TEAM_KEY"],
        "github": ["GITHUB_TOKEN", "GITHUB_REPO"],
    }
    if settings.pm_provider == "none":
        status["pm_provider"]["missing"] = ["RIPPLEBACK_PM_PROVIDER"]
    else:
        required = provider_missing.get(settings.pm_provider, [])
        missing = [name for name in required if not _env(name)]
        status["pm_provider"]["configured"] = not missing
        status["pm_provider"]["missing"] = missing
    return status
