"""Shared PM-integration contract and normalized-plan → scenario builder.

Providers fetch a project/team/repo and return an :class:`ImportedPlan` built
from :class:`ImportedEntity` / :class:`ImportedDependency` records. The builder
converts that into the exact scenario schema validated by
:func:`rippleback.scenarios.validate_scenario`.

Mapping rules are documented in ``docs/INTEGRATIONS.md``; the short version:

* entity id — the PM's stable identifier (Jira key, Linear key, GitHub
  ``#number``); the PM's opaque id is kept in ``metadata.external_id``.
* status — mapped to ``pending | in_progress | done | blocked`` with an
  explicit, configurable table (never inferred).
* dependencies — PM "blocks" links, oriented so ``dependent`` requires
  ``prerequisite``.
* effort/duration/cost — only from explicitly configured fields/labels; never
  guessed. Missing values stay missing.
* resources — one per distinct assignee, capacity from ``RESOURCES_FILE`` when
  present, else ``RIPPLEBACK_DEFAULT_CAPACITY``.
* out-of-scope links — ``RIPPLEBACK_OUT_OF_SCOPE_LINKS=error`` (reject) or
  ``stub`` (default: create a clearly labeled stub entity).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rippleback.config import load_settings


class IntegrationError(RuntimeError):
    """Raised for missing configuration, transport failures, or bad PM data."""

    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


VALID_STATUSES = ("pending", "in_progress", "done", "blocked")
DEFAULT_STATUS_MAP = {
    "new": "pending",
    "backlog": "pending",
    "unstarted": "pending",
    "todo": "pending",
    "open": "pending",
    "indeterminate": "in_progress",
    "started": "in_progress",
    "in_progress": "in_progress",
    "in progress": "in_progress",
    "done": "done",
    "completed": "done",
    "closed": "done",
    "canceled": "done",
    "cancelled": "done",
    "blocked": "blocked",
}


def parse_status_map(raw: str) -> dict[str, str]:
    """Parse ``JIRA_STATUS_MAP="Blocked:blocked,In Review:in_progress"``."""
    mapping: dict[str, str] = {}
    for part in (raw or "").split(","):
        if not part.strip() or ":" not in part:
            continue
        name, status = part.split(":", 1)
        name = name.strip()
        status = status.strip()
        if not name or status not in VALID_STATUSES:
            raise IntegrationError(
                f"invalid status mapping {part!r}; statuses must be one of "
                f"{list(VALID_STATUSES)}"
            )
        mapping[name.lower()] = status
    return mapping


def map_status(
    raw: str | None, category: str | None, overrides: dict[str, str] | None = None
) -> str:
    """Map a PM status name/category to a RippleBack status deterministically."""
    overrides = overrides or {}
    for candidate in (raw, category):
        if candidate and candidate.strip().lower() in overrides:
            return overrides[candidate.strip().lower()]
    for candidate in (raw, category):
        if candidate and candidate.strip().lower() in DEFAULT_STATUS_MAP:
            return DEFAULT_STATUS_MAP[candidate.strip().lower()]
    return "pending"


def sanitize_id(raw: str) -> str:
    """Turn a provider/project name into a stable scenario-id fragment."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip()).strip("-").lower()
    return cleaned or "plan"


@dataclass
class ImportedEntity:
    entity_id: str
    name: str
    status: str = "pending"
    external_id: str | None = None
    assignee: str | None = None
    effort: int | None = None
    duration: float | None = None
    cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportedDependency:
    dependent: str
    prerequisite: str


@dataclass
class ImportedResource:
    resource_id: str
    name: str
    capacity: int


@dataclass
class ImportedPlan:
    provider: str
    scenario_id: str
    plan_id: str
    external_id: str
    entities: list[ImportedEntity]
    dependencies: list[ImportedDependency]
    release_entity: str | None = None
    resources: list[ImportedResource] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)


def _load_resources_file() -> dict[str, int]:
    """``RESOURCES_FILE`` (YAML) → ``{resource_id: capacity}``."""
    path = load_settings().resources_file
    if not path:
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IntegrationError(
            "RESOURCES_FILE is set but PyYAML is not installed; "
            "install the project dependencies"
        ) from exc
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise IntegrationError(f"cannot read RESOURCES_FILE {path!r}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("resources", [])
    capacities: dict[str, int] = {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or "id" not in entry:
            raise IntegrationError(
                "RESOURCES_FILE entries must be dicts with an 'id' and 'capacity'"
            )
        capacity = entry.get("capacity", load_settings().default_capacity)
        capacities[str(entry["id"])] = int(capacity)
    return capacities


def build_scenario(plan: ImportedPlan) -> dict[str, Any]:
    """Convert an imported plan into a validated scenario dict."""
    entities: list[dict[str, Any]] = []
    resources: dict[str, ImportedResource] = {
        resource.resource_id: resource for resource in plan.resources
    }
    capacities = _load_resources_file()
    for imported in plan.entities:
        entity: dict[str, Any] = {
            "id": imported.entity_id,
            "name": imported.name,
            "status": imported.status,
        }
        if imported.assignee:
            entity["assignee"] = imported.assignee
            if imported.assignee not in resources:
                resources[imported.assignee] = ImportedResource(
                    resource_id=imported.assignee,
                    name=imported.assignee,
                    capacity=capacities.get(
                        imported.assignee, load_settings().default_capacity
                    ),
                )
        if imported.effort is not None:
            entity["effort"] = int(imported.effort)
        if imported.duration is not None:
            entity["duration"] = float(imported.duration)
        metadata: dict[str, Any] = dict(imported.metadata)
        if imported.external_id:
            metadata["external_id"] = imported.external_id
        if imported.cost is not None:
            metadata["cost"] = float(imported.cost)
        if metadata:
            entity["metadata"] = metadata
        entities.append(entity)
    for resource_id, resource in resources.items():
        if resource_id in capacities:
            resource.capacity = capacities[resource_id]

    constraints: list[dict[str, Any]] = []
    release = plan.release_entity or load_settings().release_entity
    if release:
        if release not in {entity.entity_id for entity in plan.entities}:
            raise IntegrationError(
                f"RIPPLEBACK_RELEASE_ENTITY {release!r} is not an imported entity"
            )
        constraints.append(
            {
                "type": "all_entities_done_before",
                "entity_ids": [release],
                "description": f"Every other entity must be done before {release}.",
            }
        )

    return {
        "scenario_id": plan.scenario_id,
        "plan_id": plan.plan_id,
        "version": 1,
        "status": "healthy",
        "entities": entities,
        "dependencies": [
            {"dependent": dep.dependent, "prerequisite": dep.prerequisite}
            for dep in plan.dependencies
        ],
        "constraints": constraints,
        **(
            {
                "resources": [
                    {
                        "id": resource.resource_id,
                        "name": resource.name,
                        "capacity": resource.capacity,
                    }
                    for resource in sorted(resources.values(), key=lambda r: r.resource_id)
                ]
            }
            if resources
            else {}
        ),
    }


class PMProvider:
    """Base provider. Subclasses implement fetch, push, and config checks."""

    name = "base"
    missing_env: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        import os

        return [name for name in self.missing_env if not (os.environ.get(name) or "").strip()]

    def require_configured(self) -> None:
        missing = self.missing()
        if missing:
            raise IntegrationError(
                f"{self.name} integration is not configured; missing: "
                f"{', '.join(missing)}",
                missing=missing,
            )

    def fetch_plan(self) -> ImportedPlan:  # pragma: no cover - abstract
        raise NotImplementedError

    def push_changes(
        self, scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str
    ) -> None:  # pragma: no cover - abstract
        raise NotImplementedError
