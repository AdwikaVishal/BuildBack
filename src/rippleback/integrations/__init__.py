"""PM provider factory and package exports.

``get_provider()`` resolves the configured ``RIPPLEBACK_PM_PROVIDER`` lazily, so
importing :mod:`rippleback.commit` never drags in an HTTP client until a write
is actually attempted. ``get_provider("jira")`` can also be called with an
explicit name (used by the outbox worker, which persists the provider name per
commit) and with an injected ``httpx.Client`` for tests.
"""

from __future__ import annotations

from typing import Any

from rippleback.config import load_settings
from rippleback.integrations.base import (
    ImportedPlan,
    IntegrationError,
    PMProvider,
    build_scenario,
)

__all__ = [
    "ImportedPlan",
    "IntegrationError",
    "PMProvider",
    "build_scenario",
    "get_provider",
    "pagerduty_ingest",
    "provider_name",
]

_PROVIDERS = {
    "jira": ("rippleback.integrations.jira", "JiraProvider"),
    "linear": ("rippleback.integrations.linear", "LinearProvider"),
    "github": ("rippleback.integrations.github", "GitHubProvider"),
}


def provider_name(name: str | None = None) -> str:
    return (name or load_settings().pm_provider).lower()


def get_provider(name: str | None = None, client: Any = None) -> PMProvider:
    """Instantiate the configured (or named) provider.

    Raises:
        IntegrationError: for an unknown/unconfigured provider, naming the
        exact environment variables that are missing.
    """
    resolved = provider_name(name)
    if resolved in ("", "none"):
        raise IntegrationError(
            "no PM provider configured; set RIPPLEBACK_PM_PROVIDER to one of "
            "jira|linear|github",
            missing=["RIPPLEBACK_PM_PROVIDER"],
        )
    entry = _PROVIDERS.get(resolved)
    if entry is None:
        raise IntegrationError(
            f"unknown RIPPLEBACK_PM_PROVIDER {resolved!r}; expected one of "
            f"{sorted(_PROVIDERS)}",
            missing=["RIPPLEBACK_PM_PROVIDER"],
        )
    module_name, class_name = entry
    module = __import__(module_name, fromlist=[class_name])
    provider_class = getattr(module, class_name)
    return provider_class(client=client) if client is not None else provider_class()


def pagerduty_ingest(client: Any = None) -> dict[str, Any]:
    """Run the PagerDuty incident ingest when configured; otherwise report missing."""
    from rippleback.integrations.pagerduty import PagerDutyProvider

    provider = PagerDutyProvider(client=client) if client is not None else PagerDutyProvider()
    try:
        return provider.ingest()
    finally:
        provider.close()
