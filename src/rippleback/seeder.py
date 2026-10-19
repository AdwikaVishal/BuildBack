"""Sample-data seeding and file import helpers.

``seed_sample`` loads the shipped ``samples/`` files into the configured
database so a fresh install has a realistic plan (with explicit substitute
candidates), incident history that lights up the risk metric, and a populated
substitute catalog. It never touches the in-process seeded scenarios, so the
demo fixtures and the test suite are unaffected.

Substitute-file format (``SUBSTITUTES_FILE`` / ``--file``): a YAML mapping of
``scenario_id`` → ``entity_id`` → list of substitute entity ids::

    sample-payments-release:
      payment-gateway: [backup-gateway]
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from rippleback import repository
from rippleback.db import session_scope
from rippleback.scenarios import ScenarioError, validate_scenario
from rippleback.security import redact


class SeedError(RuntimeError):
    """Raised when sample files are missing or malformed."""


def load_yaml(path: str | Path) -> Any:
    path = Path(path)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise SeedError("PyYAML is required to read YAML sample files") from exc
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:
        raise SeedError(f"cannot read {path}: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError
        raise SeedError(f"invalid YAML in {path}: {redact(str(exc))}") from exc


def import_substitute_file(path: str | Path, *, source: str = "import") -> dict[str, int]:
    """Replace substitute catalogs from a YAML file; returns per-scenario counts."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SeedError(
            "substitute file must be a mapping of scenario_id -> "
            "{entity_id: [substitute_entity_id, ...]}"
        )
    counts: dict[str, int] = {}
    for scenario_id, declarations in data.items():
        if not isinstance(declarations, dict):
            raise SeedError(
                f"substitute declarations for {scenario_id!r} must be a mapping "
                "of entity_id -> list"
            )
        parsed: dict[str, list[str]] = {}
        for entity_id, substitutes in declarations.items():
            if not isinstance(substitutes, list) or not all(
                isinstance(item, str) for item in substitutes
            ):
                raise SeedError(
                    f"substitutes for {scenario_id!r}/{entity_id!r} must be a list "
                    "of strings"
                )
            parsed[str(entity_id)] = [str(item) for item in substitutes]
        counts[str(scenario_id)] = repository.replace_substitute_catalog(
            str(scenario_id), parsed, source=source
        )
    return counts


def import_plan_file(path: str | Path, *, source: str = "sample") -> dict[str, Any]:
    """Import one scenario YAML file as the first version of a DB-only plan."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SeedError(f"plan file {path} must contain a scenario mapping")
    scenario = copy.deepcopy(data)
    scenario.setdefault("version", 1)
    scenario.setdefault("status", "healthy")
    errors = validate_scenario(scenario)
    if errors:
        raise SeedError(f"plan file {path} is invalid: {'; '.join(errors)}")
    with session_scope() as session:
        repository.save_version(
            session, scenario, origin="sample", version=scenario["version"],
            source=source,
        )
    return {
        "scenario_id": scenario["scenario_id"],
        "entities": len(scenario["entities"]),
        "dependencies": len(scenario["dependencies"]),
    }


def import_incident_file(path: str | Path, *, source: str = "import") -> int:
    """Import incident-history rows from a YAML list."""
    data = load_yaml(path)
    if not isinstance(data, list):
        raise SeedError(f"incident file {path} must contain a list of incidents")
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise SeedError(f"incident file {path} entry {index} must be a mapping")
        for field in ("scenario_id", "occurred_at"):
            if not entry.get(field):
                raise SeedError(
                    f"incident file {path} entry {index} is missing {field!r}"
                )
        rows.append({**entry, "source": entry.get("source", source)})
    return repository.record_incidents(rows)


def seed_sample(directory: str | Path = "samples") -> dict[str, Any]:
    """Seed plans, substitute catalogs, and incident history from ``samples/``."""
    base = Path(directory)
    if not base.is_dir():
        raise SeedError(f"sample directory {base} does not exist")
    summary: dict[str, Any] = {"plans": [], "substitutes": {}, "incidents": 0}
    plans_dir = base / "plans"
    plan_files = sorted(plans_dir.glob("*.yaml")) if plans_dir.is_dir() else []
    if not plan_files and (base / "plan.yaml").is_file():
        plan_files = [base / "plan.yaml"]
    for path in plan_files:
        summary["plans"].append(import_plan_file(path))
    substitutes = base / "substitutes.yaml"
    if substitutes.is_file():
        summary["substitutes"] = import_substitute_file(substitutes, source="sample")
    incidents = base / "incidents.yaml"
    if incidents.is_file():
        summary["incidents"] = import_incident_file(incidents, source="sample")
    if not plan_files:
        raise SeedError(
            f"no plan files found under {plans_dir}; expected *.yaml scenario files"
        )
    return summary


def ensure_scenario(scenario_id: str) -> None:
    """Raise ``SeedError`` when a scenario id is unknown."""
    try:
        repository.get_scenario(scenario_id)
    except ScenarioError as exc:
        raise SeedError(str(exc)) from exc
