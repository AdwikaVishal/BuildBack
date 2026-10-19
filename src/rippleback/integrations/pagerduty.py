"""PagerDuty incident ingestion for the risk metric.

Configuration: ``PAGERDUTY_API_KEY`` (required), ``PAGERDUTY_SERVICE_MAP``
(``SERVICE_ID:entity_id,...``) to bind services to plan entities, optional
``PAGERDUTY_SINCE_DAYS`` (default 30).

Severity mapping: ``critical→critical``, ``error→high``, ``warning→medium``,
``info→low``, anything else ``unknown``. Incidents whose service has no mapping
are stored with no entity (they never inflate risk for any option) and counted
in the returned ``unmapped`` stat so the gap is visible instead of silent.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from rippleback.config import _env, _env_int
from rippleback.integrations.base import IntegrationError, PMProvider
from rippleback.security import redact

API_ROOT = "https://api.pagerduty.com"
PAGE_SIZE = 100
MAX_PAGES = 50
MAX_RETRIES = 3
TIMEOUT_SECONDS = 20.0
SEVERITY_MAP = {
    "critical": "critical",
    "error": "high",
    "warning": "medium",
    "info": "low",
}


def parse_service_map(raw: str) -> dict[str, str]:
    """Parse ``PAGERDUTY_SERVICE_MAP="PABC123:payment-api,PXYZ789:auth-service"``."""
    mapping: dict[str, str] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        if ":" not in part:
            raise IntegrationError(
                f"invalid PAGERDUTY_SERVICE_MAP entry {part!r}; expected "
                "'SERVICE_ID:entity_id'"
            )
        service_id, entity_id = part.split(":", 1)
        service_id = service_id.strip()
        entity_id = entity_id.strip()
        if not service_id or not entity_id:
            raise IntegrationError(
                f"invalid PAGERDUTY_SERVICE_MAP entry {part!r}; both sides must be "
                "non-empty"
            )
        mapping[service_id] = entity_id
    return mapping


class PagerDutyProvider(PMProvider):
    name = "pagerduty"
    missing_env = ("PAGERDUTY_API_KEY", "PAGERDUTY_SERVICE_MAP")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=API_ROOT,
            headers={
                "Authorization": f"Token token={os.environ.get('PAGERDUTY_API_KEY', '')}",
                "Accept": "application/vnd.pagerduty+json;version=2",
            },
            timeout=TIMEOUT_SECONDS,
        )
        self._service_map = parse_service_map(_env("PAGERDUTY_SERVICE_MAP"))
        self._since_days = max(1, _env_int("PAGERDUTY_SINCE_DAYS", 30))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise IntegrationError(
                    f"pagerduty request failed: {redact(str(exc))}"
                ) from exc
            if response.status_code == 429 and attempt + 1 < MAX_RETRIES:
                try:
                    time.sleep(min(float(response.headers.get("Retry-After", "1")), 30.0))
                except ValueError:
                    time.sleep(1.0)
                continue
            if response.status_code >= 400:
                raise IntegrationError(
                    redact(
                        f"pagerduty GET {path} failed with HTTP "
                        f"{response.status_code}: {response.text[:300]}"
                    )
                )
            return response.json()
        raise IntegrationError(
            f"pagerduty request failed after retries: {redact(str(last_error))}"
        )

    def fetch_incidents(self) -> list[dict[str, Any]]:
        """Return raw incident payloads for the configured services/window."""
        self.require_configured()
        since = datetime.now(timezone.utc) - timedelta(days=self._since_days)
        params: dict[str, Any] = {
            "since": since.isoformat(timespec="seconds"),
            "limit": PAGE_SIZE,
            "offset": 0,
            "statuses[]": ["triggered", "acknowledged", "resolved"],
        }
        for service_id in sorted(self._service_map):
            params.setdefault("service_ids[]", []).append(service_id)
        incidents: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            params["offset"] = offset
            payload = self._get("/incidents", params)
            batch = payload.get("incidents") or []
            incidents.extend(batch)
            if not payload.get("has_more") or not batch:
                return incidents
            offset += len(batch)
        raise IntegrationError("pagerduty fetch exceeded the pagination safety limit")

    def ingest(self) -> dict[str, Any]:
        """Fetch incidents and store them in ``incident_history``."""
        from rippleback import repository

        incidents = self.fetch_incidents()
        rows: list[dict[str, Any]] = []
        unmapped = 0
        for incident in incidents:
            service = (incident.get("service") or {}).get("id")
            entity_id = self._service_map.get(service)
            if entity_id is None:
                unmapped += 1
            occurred_at = (
                incident.get("created_at")
                or incident.get("last_status_change_at")
                or datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            rows.append(
                {
                    "scenario_id": self._scenario_id(),
                    "entity_id": entity_id,
                    "severity": SEVERITY_MAP.get(
                        str(incident.get("severity") or "").lower(), "unknown"
                    ),
                    "occurred_at": occurred_at,
                    "source": "pagerduty",
                    "external_id": str(incident.get("id") or "") or None,
                    "summary": redact(str(incident.get("title") or "")) or None,
                }
            )
        stored = repository.record_incidents(rows)
        return {
            "provider": self.name,
            "fetched": len(incidents),
            "stored": stored,
            "unmapped": unmapped,
        }

    @staticmethod
    def _scenario_id() -> str:
        from rippleback.integrations.base import sanitize_id

        return f"pagerduty:{sanitize_id(_env('PAGERDUTY_SCENARIO_ID', 'incidents'))}"

    def fetch_plan(self) -> Any:  # pragma: no cover - incidents are not plans
        raise IntegrationError(
            "pagerduty is an incident source, not a plan provider; it feeds the "
            "risk metric only"
        )

    def push_changes(
        self, scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str
    ) -> None:  # pragma: no cover - incidents are read-only
        raise IntegrationError("pagerduty integration is read-only")
