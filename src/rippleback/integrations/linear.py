"""Linear provider (GraphQL API).

Configuration: ``LINEAR_API_KEY``, ``LINEAR_TEAM_KEY``; optional
``LINEAR_STATUS_MAP``, ``LINEAR_FIELD_EFFORT`` (default ``estimate``),
``LINEAR_BLOCKED_LABEL``.

Entity ids are the human identifiers (``ENG-123``); the GraphQL UUID travels in
``metadata.external_id`` so write-back mutations can address issues directly.
Only relations whose ``issue`` is the fetched node are read, which keeps
``dependent``/``prerequisite`` orientation deterministic without guessing.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from rippleback.config import _env, _env_int
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

API_URL = "https://api.linear.app/graphql"
MAX_PAGES = 60
PAGE_SIZE = 100
MAX_RETRIES = 3
TIMEOUT_SECONDS = 20.0

_FETCH_QUERY = """
query RippleBackIssues($teamKey: String!, $first: Int!, $after: String) {
  issues(
    filter: { team: { key: { eq: $teamKey } } },
    first: $first,
    after: $after,
    orderBy: identifier
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title estimate
      assignee { id name email }
      state { id name type }
      labels { nodes { name } }
      relations {
        id type
        issue { id identifier }
        relatedIssue { id identifier }
      }
    }
  }
}
"""

_RELATION_DELETE = """
mutation RippleBackRelationDelete($id: String!) {
  issueRelationDelete(id: $id) { success }
}
"""

_RELATION_CREATE = """
mutation RippleBackRelationCreate($issueId: String!, $relatedIssueId: String!) {
  issueRelationCreate(
    input: { issueId: $issueId, relatedIssueId: $relatedIssueId, type: blocks }
  ) { success }
}
"""

_ISSUE_UPDATE = """
mutation RippleBackIssueUpdate($id: String!, $assigneeId: String) {
  issueUpdate(input: { id: $id, assigneeId: $assigneeId }) { success }
}
"""

_RELATIONS_FETCH = """
query RippleBackRelations($id: String!) {
  issue(id: $id) {
    relations {
      id type
      issue { id identifier }
      relatedIssue { id identifier }
    }
  }
}
"""


class LinearProvider(PMProvider):
    name = "linear"
    missing_env = ("LINEAR_API_KEY", "LINEAR_TEAM_KEY")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": _env("LINEAR_API_KEY"),
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
        )
        self._team = _env("LINEAR_TEAM_KEY")
        self._status_map = parse_status_map(_env("LINEAR_STATUS_MAP"))
        self._effort_field = _env("LINEAR_FIELD_EFFORT", "estimate") or "estimate"
        self._blocked_label = (
            _env("LINEAR_BLOCKED_LABEL", "blocked") or "blocked"
        ).lower()
        self._external = len(self._client.base_url.host) > 0 and (
            "linear" not in self._client.base_url.host
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.post(
                    "", json={"query": query, "variables": variables}
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise IntegrationError(
                    f"linear request failed: {redact(str(exc))}"
                ) from exc
            if response.status_code == 429:
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(float(response.headers.get("Retry-After", "1") or 1))
                    continue
            if response.status_code >= 400:
                raise IntegrationError(
                    redact(f"linear HTTP {response.status_code}: {response.text[:300]}")
                )
            payload = response.json()
            if payload.get("errors"):
                raise IntegrationError(
                    redact(f"linear GraphQL error: {payload['errors']}")
                )
            return payload.get("data") or {}
        raise IntegrationError(
            f"linear request failed after retries: {redact(str(last_error))}"
        )

    def fetch_plan(self) -> ImportedPlan:
        self.require_configured()
        entities: list[ImportedEntity] = []
        dependencies: dict[tuple[str, str], ImportedDependency] = {}
        after: str | None = None
        for _ in range(MAX_PAGES):
            data = self._gql(
                _FETCH_QUERY,
                {"teamKey": self._team, "first": PAGE_SIZE, "after": after},
            )
            issues = (data.get("issues") or {})
            for node in issues.get("nodes") or []:
                identifier = str(node.get("identifier") or "").strip()
                if not identifier:
                    continue
                state = node.get("state") or {}
                labels = [
                    str(label.get("name", "")).lower()
                    for label in (node.get("labels") or {}).get("nodes") or []
                ]
                mapped = map_status(
                    state.get("name"), state.get("type"), self._status_map
                )
                if mapped != "done" and self._blocked_label in labels:
                    mapped = "blocked"
                assignee = node.get("assignee") or {}
                assignee_id = assignee.get("id") or None
                estimate = node.get(self._effort_field)
                effort = (
                    int(round(float(estimate)))
                    if isinstance(estimate, (int, float))
                    and not isinstance(estimate, bool)
                    else None
                )
                entity = ImportedEntity(
                    entity_id=identifier,
                    name=str(node.get("title") or identifier),
                    status=mapped,
                    external_id=str(node.get("id") or identifier),
                    assignee=assignee_id,
                    effort=effort,
                    metadata={"state": state.get("name")} if state.get("name") else {},
                )
                entities.append(entity)
                for relation in node.get("relations") or []:
                    if relation.get("type") != "blocks":
                        continue
                    subject = relation.get("issue") or {}
                    related = relation.get("relatedIssue") or {}
                    if subject.get("id") != node.get("id"):
                        continue
                    dependent = related.get("identifier")
                    prerequisite = subject.get("identifier")
                    if dependent and prerequisite:
                        dependencies[
                            (str(dependent), str(prerequisite))
                        ] = ImportedDependency(
                            dependent=str(dependent), prerequisite=str(prerequisite)
                        )
            page = issues.get("pageInfo") or {}
            if not page.get("hasNextPage") or not page.get("endCursor"):
                break
            after = page["endCursor"]
        else:
            raise IntegrationError("linear search exceeded the pagination safety limit")

        return ImportedPlan(
            provider=self.name,
            scenario_id=f"linear:{sanitize_id(self._team)}",
            plan_id=f"linear-{sanitize_id(self._team)}",
            external_id=self._team,
            entities=entities,
            dependencies=list(dependencies.values()),
        )

    # ── Write-back ───────────────────────────────────────────────────────────

    @staticmethod
    def _external_id(scenario: dict[str, Any], entity_id: str) -> str:
        for entity in scenario["entities"]:
            if entity["id"] == entity_id:
                external = (entity.get("metadata") or {}).get("external_id")
                return str(external or entity_id)
        raise IntegrationError(f"entity {entity_id!r} has no external id")

    def _relation_between(
        self, scenario: dict[str, Any], dependent: str, prerequisite: str
    ) -> str | None:
        data = self._gql(
            _RELATIONS_FETCH,
            {"id": self._external_id(scenario, dependent)},
        )
        issue = data.get("issue") or {}
        subject = self._external_id(scenario, prerequisite)
        related = self._external_id(scenario, dependent)
        for relation in issue.get("relations") or []:
            if relation.get("type") != "blocks":
                continue
            if (relation.get("issue") or {}).get("id") == subject and (
                relation.get("relatedIssue") or {}
            ).get("id") == related:
                return str(relation.get("id"))
        return None

    def push_changes(
        self, scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str
    ) -> None:
        self.require_configured()
        for change in changes:
            action = change.get("action")
            if action == "reassign":
                self._gql(
                    _ISSUE_UPDATE,
                    {
                        "id": self._external_id(scenario, change["entity_id"]),
                        "assigneeId": change["resource_id"],
                    },
                )
            elif action == "add_dependency":
                self._gql(
                    _RELATION_CREATE,
                    {
                        "issueId": self._external_id(scenario, change["prerequisite"]),
                        "relatedIssueId": self._external_id(
                            scenario, change["dependent"]
                        ),
                    },
                )
            elif action == "remove_dependency":
                relation_id = self._relation_between(
                    scenario, change["dependent"], change["prerequisite"]
                )
                if relation_id is None:
                    continue
                self._gql(_RELATION_DELETE, {"id": relation_id})
            else:
                raise IntegrationError(f"linear write-back does not support {action!r}")
