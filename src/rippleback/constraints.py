"""Hard-constraint validation for a proposed change set (Person 3, Milestone 2).

This module is **pure**: it validates a scenario and an optional change set
against a *copy* of the plan's dependencies and never mutates the caller's
scenario, its entities, dependencies, statuses, constraints, or version.
Identical input yields an identical report.

What is validated
-----------------
1. **Structural scenario validation** -- delegated to
   :func:`rippleback.scenarios.validate_scenario` (schema fields, statuses,
   references, constraint shape). A malformed *input* scenario is a caller
   error and raises :class:`ConstraintError`; nothing is guessed.
2. **Graph rules on the proposed plan** -- the change set is applied with
   :func:`rippleback.triage.apply_changes` (unknown actions and malformed
   changes raise) and the result is checked with
   :func:`rippleback.graph.graph_errors`, which supplies the stable RippleBack
   error codes (``CYCLIC_DEPENDENCY``, ``ENTITY_NOT_FOUND``,
   ``INVALID_DEPENDENCY``, ``INVALID_DEPENDENCY_TYPE``). A cycle, dangling
   reference, or invalid dependency therefore makes the report infeasible --
   never a silent pass.
3. **Semantic constraint checks** -- each entry in ``scenario["constraints"]``
   is evaluated against its documented schema (``type``, ``entity_ids``,
   optional ``description``).

Supported constraint types
--------------------------
The repository documents exactly one type (``docs/data-contract.md`` §1:
"Known ``type`` value: ``all_entities_done_before``"; fixtures in
:mod:`rippleback.scenarios` carry only this type):

* ``all_entities_done_before`` -- ``entity_ids`` are *gate* entities. The
  fixture description ("Every other entity must be done before
  production-release.") is enforced as a **current-state invariant**: no gate
  may have status ``done`` while any other entity in the scenario is not
  ``done``. A gate that is not ``done`` has not triggered the rule, so the
  constraint is reported ``satisfied`` ("no violation in the current committed
  state" -- not a claim about future state transitions; see assumptions).

Unsupported types are reported as violations (``UNSUPPORTED_CONSTRAINT``) and
make the report infeasible -- they are never skipped or assumed satisfied, so
the validator makes no false guarantees.

Errors vs violations
--------------------
Invalid *requests* raise :class:`ConstraintError`: a structurally malformed
scenario, ``changes`` that is not a list, or a malformed/unsupported change
action (wrapped from :class:`rippleback.triage.TriageError`). Well-formed
inputs that produce an invalid *plan* or break a *constraint* return an
in-band report with ``feasible`` ``False`` and a deterministic ``violations``
list -- one entry per fault, each carrying a stable ``code``, a clear
``message``, a ``source`` (``"graph"`` or ``"constraint"``), and the relevant
``entity_ids`` where applicable.

Scope
-----
Validation only. This module does not score, rank, rank recovery options,
evaluate recovery proposals, or register MCP tools; ``evaluate_recovery_plan``
verdicts and recovery-option ids are untouched.
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.graph import graph_errors
from rippleback.scenarios import validate_scenario
from rippleback.triage import TriageError, apply_changes

CONSTRAINT_ALL_ENTITIES_DONE_BEFORE = "all_entities_done_before"
SUPPORTED_CONSTRAINT_TYPES = (CONSTRAINT_ALL_ENTITIES_DONE_BEFORE,)

SOURCE_GRAPH = "graph"
SOURCE_CONSTRAINT = "constraint"

CODE_CONSTRAINT_VIOLATED = "CONSTRAINT_VIOLATED"
CODE_UNSUPPORTED_CONSTRAINT = "UNSUPPORTED_CONSTRAINT"

STATUS_SATISFIED = "satisfied"
STATUS_VIOLATED = "violated"
STATUS_UNSUPPORTED = "unsupported"


class ConstraintError(ValueError):
    """Raised for invalid validation requests (malformed scenario or changes)."""


def _malformed_scenario_error(errors: list[str]) -> ConstraintError:
    """Build the malformed-scenario error, mirroring ``get_scenario`` phrasing."""
    return ConstraintError(f"malformed scenario: {'; '.join(errors)}")


def _evaluate_all_entities_done_before(
    scenario: dict[str, Any], index: int, constraint: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Check one ``all_entities_done_before`` constraint.

    Returns the constraint's status plus any violation entries. Each gate in
    ``entity_ids`` must not be ``done`` while any other entity is not ``done``;
    offenders follow scenario entity order for determinism.
    """
    entity_ids = scenario["entities"]
    status_by_id = {entity["id"]: entity["status"] for entity in entity_ids}
    gates = constraint["entity_ids"]
    violations: list[dict[str, Any]] = []
    violated_gates: list[str] = []
    for gate in gates:
        if status_by_id[gate] != "done":
            continue
        offenders = [
            entity["id"]
            for entity in entity_ids
            if entity["id"] != gate and entity["status"] != "done"
        ]
        if not offenders:
            continue
        violated_gates.append(gate)
        violations.append(
            {
                "code": CODE_CONSTRAINT_VIOLATED,
                "message": (
                    f"constraints[{index}] '{CONSTRAINT_ALL_ENTITIES_DONE_BEFORE}': "
                    f"gate {gate!r} is done while {len(offenders)} other "
                    f"entit{'y is' if len(offenders) == 1 else 'ies are'} not "
                    f"done: {offenders}."
                ),
                "source": SOURCE_CONSTRAINT,
                "constraint_index": index,
                "constraint_type": constraint["type"],
                "entity_ids": [gate, *offenders],
            }
        )
    if violated_gates:
        return STATUS_VIOLATED, violations
    return STATUS_SATISFIED, []


def _evaluate_constraints(
    scenario: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate every committed constraint; returns (entries, violations)."""
    entries: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for index, constraint in enumerate(scenario["constraints"]):
        entry: dict[str, Any] = {
            "index": index,
            "type": constraint["type"],
            "entity_ids": copy.deepcopy(constraint["entity_ids"]),
            "description": constraint.get("description"),
            "status": STATUS_SATISFIED,
        }
        if constraint["type"] == CONSTRAINT_ALL_ENTITIES_DONE_BEFORE:
            known_ids = {entity["id"] for entity in scenario["entities"]}
            for gate in constraint["entity_ids"]:
                if not isinstance(gate, str) or gate not in known_ids:
                    raise ConstraintError(
                        f"constraints[{index}].entity_ids must name existing "
                        f"entity ids; got {gate!r}. Cannot evaluate the "
                        "constraint."
                    )
            status, found = _evaluate_all_entities_done_before(
                scenario, index, constraint
            )
        else:
            status = STATUS_UNSUPPORTED
            found = [
                {
                    "code": CODE_UNSUPPORTED_CONSTRAINT,
                    "message": (
                        f"constraints[{index}] has unsupported type "
                        f"{constraint['type']!r}; supported: "
                        f"{list(SUPPORTED_CONSTRAINT_TYPES)}. Feasibility cannot "
                        "be claimed without evaluating it."
                    ),
                    "source": SOURCE_CONSTRAINT,
                    "constraint_index": index,
                    "constraint_type": constraint["type"],
                    "entity_ids": copy.deepcopy(constraint["entity_ids"]),
                }
            ]
        entry["status"] = status
        entries.append(entry)
        violations.extend(found)
    return entries, violations


def validate_hard_constraints(
    scenario: dict[str, Any],
    *,
    changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate hard constraints and graph rules for a proposed change set.

    ``changes`` (default: none) is a list in the
    :func:`rippleback.triage.apply_changes` shape, applied to a copy of the
    committed dependencies; the caller's scenario is never modified. The
    committed plan with no changes is the baseline validation.

    Raises:
        ConstraintError: when the input scenario is structurally malformed
            (via ``scenarios.validate_scenario``), ``changes`` is not a list,
            or a change is malformed/unsupported (via ``triage.apply_changes``,
            e.g. an unknown action).

    Returns a deterministic report: ``feasible`` is ``True`` only when the
    proposed dependency set passes every graph rule **and** every committed
    constraint is supported and currently satisfied; otherwise ``violations``
    lists each fault in a stable order (graph rules first, then constraints in
    fixture order).
    """
    if changes is not None and not isinstance(changes, list):
        raise ConstraintError(
            f"changes must be a list of change dicts, got {type(changes).__name__}"
        )
    proposed_changes = copy.deepcopy(changes) if changes is not None else []

    errors = validate_scenario(scenario)
    if errors:
        raise _malformed_scenario_error(errors)

    try:
        proposed_dependencies = apply_changes(scenario, proposed_changes)
    except TriageError as exc:
        raise ConstraintError(str(exc)) from exc
    proposed = {**scenario, "dependencies": proposed_dependencies}

    graph_faults = graph_errors(proposed)
    graph_violations = [
        {
            "code": fault["code"],
            "message": fault["message"],
            "source": SOURCE_GRAPH,
            "constraint_index": None,
            "constraint_type": None,
            "entity_ids": [],
        }
        for fault in graph_faults
    ]

    constraint_entries, constraint_violations = _evaluate_constraints(proposed)
    violations = graph_violations + constraint_violations
    feasible = not violations

    assumptions = [
        "The change set is applied to a copy of the committed dependencies with "
        "rippleback.triage.apply_changes semantics; the committed plan, entity "
        "statuses, and version are never modified.",
        "'all_entities_done_before' is checked as a current-state invariant: a "
        "listed gate must not be 'done' while any other entity is not 'done'. "
        "'satisfied' means no violation exists in the current committed state, "
        "not a guarantee about future state transitions.",
        "Constraint evaluation reads committed entity statuses; change sets "
        "modify dependencies only and can never change a status.",
        "'feasible: true' means every graph rule passed and every constraint "
        "was supported and currently satisfied.",
    ]
    limitations = [
        "No schedule or calendar is modeled: 'done before' is evaluated from "
        "committed statuses and dependency structure, never from wall-clock "
        "time.",
        "The dependency graph is not required to structurally force the "
        "ordering -- an entity with no hard path to a gate could still finish "
        "after it; only current statuses are checked.",
        "Unsupported constraint types are reported as violations and make the "
        "report infeasible; they are never silently skipped or assumed "
        "satisfied.",
        "Validation only: no scoring, ranking, substitution candidates, or "
        "recovery-option evaluation happens here.",
    ]
    if any(fault["source"] == SOURCE_GRAPH for fault in violations):
        limitations.append(
            "The proposed dependency set is structurally invalid, so its "
            "constraints are reported but no valid plan is implied."
        )

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "changes": proposed_changes,
        "feasible": feasible,
        "graph_errors": graph_faults,
        "constraints": constraint_entries,
        "violations": violations,
        "assumptions": assumptions,
        "limitations": limitations,
    }
