"""GitHub provider (REST API, issues only).

Configuration: ``GITHUB_TOKEN``, ``GITHUB_REPO`` (``owner/repo``); optional
``GITHUB_STATUS_MAP``, ``GITHUB_LABEL_EFFORT_PREFIX`` (default ``effort:``),
``GITHUB_LABEL_DURATION_PREFIX`` (default ``duration:``), ``GITHUB_BLOCKED_LABEL``
(default ``blocked``), ``GITHUB_BLOCKED_BY_PATTERN``.

Dependency discovery reads a documented body line, e.g. ``Blocked by: #12, #34``
(or ``Depends on:``). Links to issues outside the imported set follow
``RIPPLEBACK_OUT_OF_SCOPE_LINKS``: ``error`` rejects the import, ``stub``
(default) creates a labeled stub entity.

Write-back reassigns the assignees array, and manages dependencies inside a
dedicated body block (``<!-- rippleback:deps -->``) so the rest of the issue
body is never rewritten.
"""

from __future__ import annotations

import os
import re
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

API_ROOT = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGES = 50
MAX_RETRIES = 3
TIMEOUT_SECONDS = 20.0
DEPS_BLOCK_START = "<!-- rippleback:deps:start -->"
DEPS_BLOCK_END = "<!-- rippleback:deps:end -->"
DEFAULT_BLOCKED_BY_PATTERN = r"(?im)^\s*(?:blocked\s*by|depends\s*on)\s*:\s*(.+)$"
ISSUE_REF_PATTERN = re.compile(r"(?:#(\d+)|(?:https?://\S+/issues/)(\d+))")


class GitHubProvider(PMProvider):
    name = "github"
    missing_env = ("GITHUB_TOKEN", "GITHUB_REPO")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._repo = _env("GITHUB_REPO")
        self._client = client or httpx.Client(
            base_url=API_ROOT,
            headers={
                "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=TIMEOUT_SECONDS,
        )
        self._status_map = parse_status_map(_env("GITHUB_STATUS_MAP"))
        self._blocked_label = (
            _env("GITHUB_BLOCKED_LABEL", "blocked") or "blocked"
        ).lower()
        self._effort_prefix = (
            _env("GITHUB_LABEL_EFFORT_PREFIX", "effort:") or "effort:"
        ).lower()
        self._duration_prefix = (
            _env("GITHUB_LABEL_DURATION_PREFIX", "duration:") or "duration:"
        ).lower()
        try:
            self._blocked_by = re.compile(
                _env("GITHUB_BLOCKED_BY_PATTERN", DEFAULT_BLOCKED_BY_PATTERN)
            )
        except re.error as exc:
            raise IntegrationError(
                f"invalid GITHUB_BLOCKED_BY_PATTERN: {redact(str(exc))}"
            ) from exc

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
                    f"github request failed: {redact(str(exc))}"
                ) from exc
            if response.status_code in (429, 503) and attempt + 1 < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    time.sleep(min(float(retry_after), 30.0))
                except ValueError:
                    time.sleep(1.0)
                continue
            if response.status_code >= 400:
                raise IntegrationError(
                    redact(
                        f"github {method} {path} failed with HTTP "
                        f"{response.status_code}: {response.text[:300]}"
                    )
                )
            return response
        raise IntegrationError(
            f"github request failed after retries: {redact(str(last_error))}"
        )

    # ── Fetch ────────────────────────────────────────────────────────────────

    @staticmethod
    def _number_value(label: str, prefix: str) -> float | None:
        lowered = label.lower()
        if not lowered.startswith(prefix):
            return None
        raw = lowered[len(prefix) :].strip()
        try:
            return float(raw)
        except ValueError:
            return None

    def _blocked_by_refs(self, body: str | None) -> list[int]:
        if not body:
            return []
        refs: list[int] = []
        for match in self._blocked_by.finditer(body):
            for token in match.group(1).split(","):
                for number_match in ISSUE_REF_PATTERN.finditer(token.strip()):
                    number = number_match.group(1) or number_match.group(2)
                    if number and int(number) not in refs:
                        refs.append(int(number))
        return refs

    def fetch_plan(self) -> ImportedPlan:
        self.require_configured()
        issues: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            response = self._request(
                "GET",
                f"/repos/{self._repo}/issues",
                params={
                    "state": "all",
                    "per_page": PAGE_SIZE,
                    "page": page,
                    "sort": "created",
                    "direction": "asc",
                },
            )
            batch = [
                item
                for item in response.json()
                if isinstance(item, dict) and "pull_request" not in item
            ]
            issues.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        else:
            raise IntegrationError("github fetch exceeded the pagination safety limit")

        strict = _env("RIPPLEBACK_OUT_OF_SCOPE_LINKS", "stub").lower() == "error"
        entities: list[ImportedEntity] = []
        known_numbers: set[int] = set()
        for issue in issues:
            if isinstance(issue.get("number"), int):
                known_numbers.add(issue["number"])

        dependencies: dict[tuple[str, str], ImportedDependency] = {}
        out_of_scope: list[str] = []
        by_number: dict[int, ImportedEntity] = {}
        for issue in issues:
            number = int(issue["number"])
            labels = [str(label.get("name", "")).lower() for label in issue.get("labels") or []]
            mapped = map_status(
                "open" if issue.get("state") == "open" else "closed",
                None,
                self._status_map,
            )
            if mapped != "done" and self._blocked_label in labels:
                mapped = "blocked"
            effort = next(
                (
                    self._number_value(label, self._effort_prefix)
                    for label in labels
                    if self._number_value(label, self._effort_prefix) is not None
                ),
                None,
            )
            duration = next(
                (
                    self._number_value(label, self._duration_prefix)
                    for label in labels
                    if self._number_value(label, self._duration_prefix) is not None
                ),
                None,
            )
            assignee = (issue.get("assignee") or {}).get("login") or None
            entity = ImportedEntity(
                entity_id=f"#{number}",
                name=str(issue.get("title") or f"#{number}"),
                status=mapped,
                external_id=str(number),
                assignee=assignee,
                effort=int(round(effort)) if effort is not None else None,
                duration=duration,
                metadata={"html_url": issue.get("html_url")} if issue.get("html_url") else {},
            )
            entities.append(entity)
            by_number[number] = entity

        for issue in issues:
            dependent_number = int(issue["number"])
            for referenced in self._blocked_by_refs(issue.get("body")):
                if referenced not in by_number:
                    if strict:
                        raise IntegrationError(
                            f"issue #{dependent_number} is blocked by out-of-scope "
                            f"issue #{referenced}; set "
                            f"RIPPLEBACK_OUT_OF_SCOPE_LINKS=stub to keep it"
                        )
                    if referenced not in out_of_scope:
                        out_of_scope.append(referenced)
                        stub = ImportedEntity(
                            entity_id=f"#{referenced}",
                            name=f"External: #{referenced}",
                            status="pending",
                            external_id=str(referenced),
                            metadata={"external": True, "out_of_scope": True},
                        )
                        entities.append(stub)
                        by_number[referenced] = stub
                dependencies[(f"#{dependent_number}", f"#{referenced}")] = ImportedDependency(
                    dependent=f"#{dependent_number}", prerequisite=f"#{referenced}"
                )

        scenario_id = f"github:{sanitize_id(self._repo)}"
        return ImportedPlan(
            provider=self.name,
            scenario_id=scenario_id,
            plan_id=f"github-{sanitize_id(self._repo)}",
            external_id=self._repo,
            entities=entities,
            dependencies=list(dependencies.values()),
            out_of_scope=out_of_scope,
        )

    # ── Write-back ───────────────────────────────────────────────────────────

    @staticmethod
    def _number_of(scenario: dict[str, Any], entity_id: str) -> int:
        for entity in scenario["entities"]:
            if entity["id"] == entity_id:
                raw = (entity.get("metadata") or {}).get("external_id")
                if raw is None:
                    raw = entity_id.lstrip("#")
                try:
                    return int(raw)
                except (TypeError, ValueError) as exc:
                    raise IntegrationError(
                        f"entity {entity_id!r} has no issue number for write-back"
                    ) from exc
        raise IntegrationError(f"entity {entity_id!r} is not part of this plan")

    @staticmethod
    def _deps_block(refs: list[int]) -> str:
        ordered = sorted(set(refs))
        line = f"Blocked by: {', '.join(f'#{n}' for n in ordered)}"
        return f"{DEPS_BLOCK_START}\n{line}\n{DEPS_BLOCK_END}"

    @classmethod
    def _block_refs(cls, body: str) -> list[int]:
        start = body.find(DEPS_BLOCK_START)
        end = body.find(DEPS_BLOCK_END)
        if start < 0 or end < 0 or end < start:
            return []
        block = body[start + len(DEPS_BLOCK_START) : end]
        refs: list[int] = []
        for match in ISSUE_REF_PATTERN.finditer(block):
            number = match.group(1) or match.group(2)
            if number and int(number) not in refs:
                refs.append(int(number))
        return refs

    @classmethod
    def _rewrite_body(
        cls, body: str | None, refs: list[int], *, remove: set[int] = frozenset()
    ) -> str:
        body = body or ""
        existing = [number for number in cls._block_refs(body) if number not in remove]
        merged = sorted(set(existing) | set(refs))
        block = cls._deps_block(merged)
        start = body.find(DEPS_BLOCK_START)
        end = body.find(DEPS_BLOCK_END)
        if start >= 0 and end >= start:
            end += len(DEPS_BLOCK_END)
            updated = body[:start] + block + body[end:]
        else:
            updated = (body.rstrip() + "\n\n" + block) if body.strip() else block
        return updated.rstrip() + "\n"

    def push_changes(
        self, scenario: dict[str, Any], changes: list[dict[str, Any]], kind: str
    ) -> None:
        self.require_configured()
        for change in changes:
            action = change.get("action")
            if action == "reassign":
                number = self._number_of(scenario, change["entity_id"])
                self._request(
                    "PATCH",
                    f"/repos/{self._repo}/issues/{number}",
                    json={"assignees": [change["resource_id"]]},
                )
            elif action in {"add_dependency", "remove_dependency"}:
                number = self._number_of(scenario, change["dependent"])
                response = self._request(
                    "GET", f"/repos/{self._repo}/issues/{number}"
                )
                current_body = (response.json() or {}).get("body") or ""
                prerequisite_number = self._number_of(
                    scenario, change["prerequisite"]
                )
                if action == "add_dependency":
                    updated = self._rewrite_body(
                        current_body, {prerequisite_number}
                    )
                else:
                    updated = self._rewrite_body(
                        current_body, set(), remove={prerequisite_number}
                    )
                self._request(
                    "PATCH",
                    f"/repos/{self._repo}/issues/{number}",
                    json={"body": updated},
                )
            else:
                raise IntegrationError(f"github write-back does not support {action!r}")
