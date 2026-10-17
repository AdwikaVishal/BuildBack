"""Deterministic, read-only evaluation of a proposed recovery option.

Phase 4, first slice. Given a *proposal* -- the stable ``option_id`` produced by
:mod:`rippleback.recovery` -- this module decides whether that proposal is
**structurally applicable** to the committed scenario and describes the
remaining prerequisites and downstream effects using the residual graph.

This module is **pure**: it never mutates the committed scenario, its entity
statuses, its dependency list, or its version. Identical input yields an
identical report.

Structural validity vs business readiness
-----------------------------------------
``structurally_valid`` means only that the proposal's dependency edge exists in
the committed plan, its entity references resolve, and the caller's
``expected_plan_version`` matches the committed version. It deliberately does
**not** assert that bypassing a prerequisite is safe or ready to execute: the
scenario contains no information about integration contracts, substitute
services, or delivery constraints. Every report therefore carries an explicit
``business_readiness_not_assessed`` warning and matching limitations.

Proposal shape
--------------
The proposal is the Phase 3 option id, ``drop-dependency:{dependent}->{prerequisite}``
(see ``docs/data-contract.md`` §5). The disrupted entity is the ``prerequisite``
(parsed from the option id); the edge being removed is
``{dependent} -> {prerequisite}``. Coupling evaluation to the documented option
id format is recorded as an ambiguity; a future proposal object may pass the
edge explicitly.
"""

from __future__ import annotations

from typing import Any

from rippleback.impact import _dependents_by_prerequisite, _evidence_paths

OPTION_ID_PREFIX = "drop-dependency:"
ACTION_REMOVE_DEPENDENCY = "remove_dependency"

VERDICT_VALID = "structurally_valid"
VERDICT_REJECTED = "rejected"

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

FINDING_PLAN_VERSION_MATCH = "plan_version_match"
FINDING_STALE_PLAN_VERSION = "stale_plan_version"
FINDING_DEPENDENCY_EDGE_EXISTS = "dependency_edge_exists"
FINDING_MISSING_DEPENDENCY = "missing_dependency"
FINDING_UNKNOWN_ENTITY = "unknown_entity"
FINDING_BUSINESS_READINESS_NOT_ASSESSED = "business_readiness_not_assessed"


class EvaluationError(ValueError):
    """Raised for malformed evaluation requests (bad option id or version)."""


def _finding(code: str, level: str, message: str) -> dict[str, str]:
    return {"code": code, "level": level, "message": message}


def parse_option_id(option_id: str) -> tuple[str, str]:
    """Split a recovery ``option_id`` into ``(dependent, prerequisite)``.

    Raises :class:`EvaluationError` if the id is not a well-formed
    ``drop-dependency:{dependent}->{prerequisite}`` string. Empty or missing
    halves are rejected; a malformed id is an input error, not a verdict.
    """
    if not isinstance(option_id, str) or not option_id.startswith(OPTION_ID_PREFIX):
        raise EvaluationError(
            f"malformed option_id {option_id!r}; expected "
            f"{OPTION_ID_PREFIX!r} followed by '{{dependent}}->{{prerequisite}}'"
        )
    body = option_id[len(OPTION_ID_PREFIX):]
    parts = body.split("->")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise EvaluationError(
            f"malformed option_id {option_id!r}; expected "
            f"{OPTION_ID_PREFIX!r} followed by '{{dependent}}->{{prerequisite}}'"
        )
    return parts[0], parts[1]


def _assess_impact(
    scenario: dict[str, Any], dependent: str, prerequisite: str
) -> dict[str, Any]:
    """Assess remaining prerequisites and downstream effects on the residual graph.

    The residual graph is the committed dependencies with the proposed edge
    removed. ``unblocks`` are entities no longer reachable from the disrupted
    prerequisite; ``still_blocked`` are entities still reachable. Listing uses
    scenario entity order, so output is deterministic.
    """
    entities: list[dict[str, Any]] = scenario["entities"]
    order = [entity["id"] for entity in entities]
    committed: list[dict[str, Any]] = scenario["dependencies"]
    removed = {"dependent": dependent, "prerequisite": prerequisite}

    before_paths = _evidence_paths(prerequisite, _dependents_by_prerequisite(scenario))
    affected_before = {eid for eid in before_paths if eid != prerequisite}

    residual_dependencies = [dict(dep) for dep in committed if dep != removed]
    residual_scenario = {**scenario, "dependencies": residual_dependencies}
    after_paths = _evidence_paths(
        prerequisite, _dependents_by_prerequisite(residual_scenario)
    )
    affected_after = {eid for eid in after_paths if eid != prerequisite}

    unblocks = [
        eid for eid in order if eid in affected_before and eid not in affected_after
    ]
    still_blocked = [eid for eid in order if eid in affected_after]
    return {
        "disrupted_entity_id": prerequisite,
        "removed_dependency": removed,
        "unblocks": unblocks,
        "still_blocked": still_blocked,
    }


def evaluate_recovery_plan(
    scenario: dict[str, Any],
    *,
    option_id: str,
    expected_plan_version: int,
) -> dict[str, Any]:
    """Return a deterministic, read-only evaluation of a recovery proposal.

    The committed ``scenario`` is never modified. A malformed ``option_id`` or a
    non-integer ``expected_plan_version`` raises :class:`EvaluationError`. A
    well-formed proposal that references unknown entities, a missing dependency
    edge, or a stale plan version yields a ``rejected`` report (a documented
    result, never a fallback to a different plan).
    """
    dependent, prerequisite = parse_option_id(option_id)
    if not isinstance(expected_plan_version, int) or isinstance(
        expected_plan_version, bool
    ):
        raise EvaluationError("expected_plan_version must be an integer")

    entities: list[dict[str, Any]] = scenario["entities"]
    by_id = {entity["id"]: entity for entity in entities}
    committed: list[dict[str, Any]] = scenario["dependencies"]
    version = scenario["version"]

    findings: list[dict[str, str]] = []

    version_ok = expected_plan_version == version
    if version_ok:
        findings.append(
            _finding(
                FINDING_PLAN_VERSION_MATCH,
                LEVEL_INFO,
                f"expected_plan_version {expected_plan_version} matches the "
                f"committed plan version {version}.",
            )
        )
    else:
        findings.append(
            _finding(
                FINDING_STALE_PLAN_VERSION,
                LEVEL_ERROR,
                f"expected_plan_version {expected_plan_version} does not match the "
                f"committed plan version {version}; the proposal may be stale.",
            )
        )

    unknown = [name for name in (dependent, prerequisite) if name not in by_id]
    if unknown:
        findings.append(
            _finding(
                FINDING_UNKNOWN_ENTITY,
                LEVEL_ERROR,
                f"proposal references unknown entity id(s): {unknown}.",
            )
        )

    edge = {"dependent": dependent, "prerequisite": prerequisite}
    edge_exists = edge in committed
    if not unknown:
        if edge_exists:
            findings.append(
                _finding(
                    FINDING_DEPENDENCY_EDGE_EXISTS,
                    LEVEL_INFO,
                    f"dependency edge '{dependent}' requires '{prerequisite}' "
                    f"exists in the committed plan.",
                )
            )
        else:
            findings.append(
                _finding(
                    FINDING_MISSING_DEPENDENCY,
                    LEVEL_ERROR,
                    f"dependency edge '{dependent}' requires '{prerequisite}' "
                    f"does not exist in the committed plan.",
                )
            )

    structurally_valid = version_ok and not unknown and edge_exists
    verdict = VERDICT_VALID if structurally_valid else VERDICT_REJECTED

    impact: dict[str, Any] | None = None
    if structurally_valid:
        findings.append(
            _finding(
                FINDING_BUSINESS_READINESS_NOT_ASSESSED,
                LEVEL_WARNING,
                "Structural validity only: business readiness (integration "
                "contracts, substitute services, delivery constraints) is not "
                "assessed and must be reviewed by a human.",
            )
        )
        impact = _assess_impact(scenario, dependent, prerequisite)

    return {
        "evaluation_id": (
            f"eval:{scenario['scenario_id']}:{option_id}:v{expected_plan_version}"
        ),
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": version,
        "expected_plan_version": expected_plan_version,
        "verdict": verdict,
        "dependency": edge,
        "findings": findings,
        "impact": impact,
        "assumptions": [
            f"Assumes '{prerequisite}' is the only unavailable entity "
            f"(single-disruption model).",
            "Assumes the committed dependency graph is authoritative for "
            "dependency ordering.",
        ],
        "limitations": [
            "Structural validity does not imply business readiness; removing a "
            "hard prerequisite may break integration guarantees.",
            "No substitute candidates, cost/effort ranking, or scheduling are "
            "evaluated.",
            "Read-only: the committed plan is not changed and no recovery is "
            "committed.",
        ],
    }
