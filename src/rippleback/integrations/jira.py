"""Jira Cloud provider (REST API v3).

Configuration: ``JIRA_BASE_URL``, ``JIRA_EMAIL``, ``JIRA_API_TOKEN``,
``JIRA_PROJECT_KEY``; optional ``JIRA_JQL``, ``JIRA_STATUS_MAP``,
``JIRA_FIELD_EFFORT`` / ``_DURATION`` / ``_COST``, ``JIRA_BLOCKED_LABEL``.

The search uses the current ``/rest/api/3/search/jql`` token pagination and
falls back to the legacy ``/rest/api/3/search`` offset pagination when the new
endpoint is unavailable. ``429`` responses honor ``Retry-After``.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from rippleback.config import _env, _env_float
from rippleback.integrations.base import (
    ImportedDependency,
    ImportedEntity,
    ImportedPlan,
    IntegrationError,
    PMProvider,
    map_status,
    parse_status_map,
    sanitize_id,
)
from rippleback.security import redact

MAX_PAGES = 100
PAGE_SIZE = 100
MAX_RETRIES = 3
TIMEOUT_SECONDS = 20.0
BLOCK_LINK_NAME = "Blocks"


class JiraProvider(PMProvider):
    name = "jira"
    missing_env = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        base = _env("JIRA_BASE_URL").rstrip("/")
        self._client = client or httpx.Client(
            base_url=base,
            auth=(_env("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN", "")),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
        self._project = _env("JIRA_PROJECT_KEY")
        self._jql = _env("JIRA_JQL") or (
            f'project = "{self._project}" ORDER BY key ASC'
        )
        self._status_map = parse_status_map(_env("JIRA_STATUS_MAP"))
        self._blocked_label = (_env("JIRA_BLOCKED_LABEL", "blocked") or "blocked").lower()
        self._field_effort = _env("JIRA_FIELD_EFFORT")
        self._field_duration = _env("JIRA_FIELD_DURATION")
        self._field_cost = _env("JIRA_FIELD_COST")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── Transport ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise IntegrationError(
                    f"jira request failed: {redact(str(exc))}"
                ) from exc
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    delay = min(float(retry_after), 30.0)
                except ValueError:
                    delay = 1.0
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(delay)
                    continue
            if response.status_code >= 400:
                raise IntegrationError(self._error_message(response))
            return response
        raise IntegrationError(
            f"jira request failed after retries: {redact(str(last_error))}"
        )

    def _error_message(self, response: httpx.Response) -> str:
        detail = ""
        try:
            payload = response.json()
            messages = payload.get("errorMessages") or []
            errors = payload.get("errors") or {}
            detail = "; ".join([*messages, *[f"{k}: {v}" for k, v in errors.items()]])
        except Exception:
            detail = response.text[:300]
        return redact(
            f"jira {response.request.method} {response.request.url.path} failed "
            f"with HTTP {response.status_code}: {detail}"
        )

    # ── Fetch ────────────────────────────────────────────────────────────────

    def _fields(self) -> list[str]:
        fields = ["summary", "status", "assignee", "issuelinks", "labels"]
        for field in (self._field_effort, self._field_duration, self._field_cost):
            if field:
                fields.append(field)
        return fields

    def _fetch_issues(self) -> list[dict[str, Any]]:
        fields = self._fields()
        issues: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            body: dict[str, Any] = {
                "jql": self._jql,
                "maxResults": PAGE_SIZE,
                "fields": fields,
            }
            if token:
                body["nextPageToken"] = token
            try:
                response = self._request("POST", "/rest/api/3/search/jql", json=body)
                payload = response.json()
                batch = payload.get("issues") or []
                issues.extend(batch)
                token = payload.get("nextPageToken")
                if payload.get("isLast", True) or not token:
                    return issues
            except IntegrationError as exc:
                if "HTTP 404" not in str(exc) and "HTTP 410" not in str(exc):
                    raise
                return self._fetch_issues_legacy(fields)
        raise IntegrationError("jira search exceeded the pagination safety limit")

    def _fetch_issues_legacy(self, fields: list[str]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start_at = 0
        for _ in range(MAX_PAGES):
            response = self._request(
                "GET",
                "/rest/api/3/search",
                params={
                    "jql": self._jql,
                    "startAt": start_at,
                    "maxResults": PAGE_SIZE,
                    "fields": ",".join(fields),
                },
            )
            payload = response.json()
            batch = payload.get("issues") or []
            issues.extend(batch)
            start_at += len(batch)
            total = int(payload.get("total", start_at))
            if not batch or start_at >= total:
                return issues
        raise IntegrationError("jira search exceeded the pagination safety limit")

    def _assignee(self, raw: Any) -> tuple[str | None, str | None]:
        if not isinstance(raw, dict):
            return None, None
        identifier = (
            raw.get("accountId")
            or raw.get("emailAddress")
            or raw.get("displayName")
        )
        if not identifier:
            return None, None
        return str(identifier), raw.get("displayName") or str(identifier)

    def _number(self, fields: dict[str, Any], name: str) -> float | None:
        if not name:
            return None
        value = fields.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    def fetch_plan(self) -> ImportedPlan:
        self.require_configured()
        issues = self._fetch_issues()
        entities: list[ImportedEntity] = []
        by_key: dict[str, ImportedEntity] = {}
        raw_by_key: dict[str, dict[str, Any]] = {}
        for issue in issues:
            key = str(issue.get("key") or "").strip()
            if not key:
                continue
            fields = issue.get("fields") or {}
            status = fields.get("status") or {}
            category = (status.get("statusCategory") or {}).get("key")
            mapped = map_status(status.get("name"), category, self._status_map)
            labels = [str(label).lower() for label in fields.get("labels") or []]
            if mapped != "done" and self._blocked_label in labels:
                mapped = "blocked"
            assignee_id, _assignee_name = self._assignee(fields.get("assignee"))
            effort = self._number(fields, self._field_effort)
            duration = self._number(fields, self._field_duration)
            cost = self._number(fields, self._field_cost)
            entity = ImportedEntity(
                entity_id=key,
                name=str(fields.get("summary") or key),
                status=mapped,
                external_id=str(issue.get("id") or key),
                assignee=assignee_id,
                effort=int(round(effort)) if effort is not None else None,
                duration=duration,
                cost=cost,
            )
            entities.append(entity)
            by_key[key] = entity
            raw_by_key[key] = fields

        strict = _env("RIPPLEBACK_OUT_OF_SCOPE_LINKS", "stub").lower() == "error"
        dependencies: dict[tuple[str, str], ImportedDependency] = {}
        out_of_scope: list[str] = []
        for key, fields in raw_by_key.items():
            for link in fields.get("issuelinks") or []:
                link_type = (link.get("type") or {}).get("name", "")
                if "block" not in link_type.lower():
                    continue
                pairs: list[tuple[str, str]] = []
                if link.get("outwardIssue"):
                    pairs.append((key, link["outwardIssue"].get("key")))
                if link.get("inwardIssue"):
                    pairs.append((link["inwardIssue"].get("key"), key))
                for dependent, prerequisite in pairs:
                    if dependent is None or prerequisite is None:
                        continue
                    for referenced in (dependent, prerequisite):
                        if referenced not in by_key:
                            if strict:
                                raise IntegrationError(
                                    f"out-of-scope dependency link references "
                                    f"{referenced!r}, which is not part of the "
                                    f"imported project; set "
                                    f"RIPPLEBACK_OUT_OF_SCOPE_LINKS=stub to keep it"
                                )
                            if referenced not in out_of_scope:
                                out_of_scope.append(referenced)
                                stub = ImportedEntity(
                                    entity_id=str(referenced),
                                    name=f"External: {referenced}",
                                    status="pending",
                                    external_id=str(referenced),
                                    metadata={"external": True, "out_of_scope": True},
                                )
                                entities.append(stub)
                                by_key[str(referenced)] = stub
                    dependencies[(dependent, prerequisite)] = ImportedDependency(
                        dependent=dependent, prerequisite=prerequisite
                    )

        return ImportedPlan(
            provider=self.name,
            scenario_id=f"jira:{sanitize_id(self._project)}",
            plan_id=f"jira-{sanitize_id(self._project)}",
            external_id=self._project,
            entities=entities,
            dependencies=list(dependencies.values()),
            out_of_scope=out_of_scope,
        )

    # ── Write-back ───────────────────────────────────────────────────────────

    def _link_id(self, dependent: str, prerequisite: str) -> str | None:
        response = self._request(
            "GET", f"/rest/api/3/issue/{dependent}", params={"fields": "issuelinks"}
        )
        fields = (response.json() or {}).get("fields") or {}
        for link in fields.get("issuelinks") or []:
            link_type = (link.get("type") or {}).get("name", "")
            if "block" not in link_type.lower():
                continue
            outward = (link.get("outwardIssue") or {}).get("key")
            inward = (link.get("inwardIssue") or {}).get("key")
            if outward == prerequisite and (inward in (None, dependent)):
                return str(link.get("id"))
            if inward == dependent and outward == prerequisite:
                return str(link.get("id"))
        return None

    def push_changes(
        self, scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str
    ) -> None:
        self.require_configured()
        for change in changes:
            action = change.get("action")
            if action == "reassign":
                self._request(
                    "PUT",
                    f"/rest/api/3/issue/{change['entity_id']}/assignee",
                    json={"accountId": change["resource_id"]},
                )
            elif action == "add_dependency":
                self._request(
                    "POST",
                    "/rest/api/3/issueLink",
                    json={
                        "type": {"name": BLOCK_LINK_NAME},
                        "outwardIssue": {"key": change["prerequisite"]},
                        "inwardIssue": {"key": change["dependent"]},
                    },
                )
            elif action == "remove_dependency":
                link_id = self._link_id(change["dependent"], change["prerequisite"])
                if link_id is None:
                    continue
                self._request("DELETE", f"/rest/api/3/issueLink/{link_id}")
            else:
                raise IntegrationError(f"jira write-back does not support {action!r}")
