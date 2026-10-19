"""Weight resolution and derived cost/risk/disruption metrics.

The engine is pure and must stay that way: everything here exercises
``metrics`` (the adapter from configured data sources) rather than the scorer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from rippleback import repository
from rippleback.db import session_scope
from rippleback.metrics import (
    METRIC_KEYS,
    affected_entities,
    derive_disruption_count,
    derive_metrics,
    derive_risk,
    resolve_weights,
)
from rippleback.scoring import DEFAULT_WEIGHTS

WEIGHT_ENV_VARS = (
    "SCORE_WEIGHT_DELAY",
    "SCORE_WEIGHT_COST",
    "SCORE_WEIGHT_RISK",
    "SCORE_WEIGHT_STRAIN",
    "SCORE_WEIGHT_DISRUPTION",
    "COST_RATE_PER_WORK_UNIT",
    "COST_PER_CHANGE",
)


@pytest.fixture(autouse=True)
def clean_scoring_env(monkeypatch: pytest.MonkeyPatch):
    for name in WEIGHT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


SCENARIO = {
    "scenario_id": None,  # filled per test
    "plan_id": "plan-metrics",
    "version": 1,
    "status": "healthy",
    "entities": [
        {"id": "a", "name": "A", "status": "in_progress", "effort": 2},
        {"id": "b", "name": "B", "status": "pending", "effort": 3},
        {"id": "c", "name": "C", "status": "done", "effort": 5},
        {"id": "d", "name": "D", "status": "pending", "effort": 1},
    ],
    "resources": [
        {"id": "r1", "name": "R1", "capacity": 1},
        {"id": "r2", "name": "R2", "capacity": 1},
    ],
    # a -> b -> d chain, plus an unrelated entity c (done)
    "dependencies": [
        {"dependent": "b", "prerequisite": "a"},
        {"dependent": "d", "prerequisite": "b"},
    ],
    "constraints": [],
}

DROP = {"action": "remove_dependency", "dependent": "b", "prerequisite": "a"}


def _materialize(entities=None) -> dict[str, Any]:
    import copy

    sid = f"metrics-{uuid.uuid4().hex[:12]}"
    scenario = copy.deepcopy(SCENARIO)
    scenario["scenario_id"] = sid
    scenario["plan_id"] = f"plan-{sid}"
    if entities is not None:
        scenario["entities"] = entities
    with session_scope() as session:
        repository.save_version(session, scenario, origin="test", version=1, source="test")
    return scenario


def _options(*option_ids: str) -> list[dict[str, Any]]:
    return [
        {
            "option_id": f"drop-dependency:{opt}",
            "changes": [DROP],
            "label": opt,
        }
        for opt in option_ids
    ]


def _incident(scenario_id: str, entity_id, severity: str, days_ago: float) -> None:
    occurred = datetime.now(timezone.utc) - timedelta(days=days_ago)
    repository.record_incidents(
        [
            {
                "scenario_id": scenario_id,
                "entity_id": entity_id,
                "severity": severity,
                "occurred_at": occurred.isoformat(),
                "source": "test",
                "external_id": f"{scenario_id}-{entity_id}-{days_ago}",
            }
        ]
    )


# ── affected entities ─────────────────────────────────────────────────────────


class TestAffectedEntities:
    def test_dependency_change_pulls_in_transitive_dependents(self) -> None:
        scenario = _materialize()
        assert affected_entities(scenario, [DROP]) == {"a", "b", "d"}

    def test_reassignment_only_affects_the_entity(self) -> None:
        scenario = _materialize()
        affected = affected_entities(
            scenario, [{"action": "reassign", "entity_id": "a", "resource_id": "r2"}]
        )
        assert affected == {"a", "b", "d"}

    def test_non_dict_changes_are_ignored(self) -> None:
        scenario = _materialize()
        assert affected_entities(scenario, ["nonsense"]) == set()


# ── defaults / gating ─────────────────────────────────────────────────────────


class TestResolveWeights:
    def test_pristine_demo_keeps_historical_defaults(self) -> None:
        scenario = _materialize()
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights == DEFAULT_WEIGHTS
        assert sources["delay"] == "default"
        assert sources["resource_strain"] == "disabled_opt_in"
        assert sources["cost"] == "disabled_no_source"
        assert sources["risk"] == "disabled_no_source"
        assert sources["disruption_count"] == "disabled_no_source"

    def test_env_weight_always_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _materialize()
        monkeypatch.setenv("SCORE_WEIGHT_RISK", "2.5")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["risk"] == 2.5
        assert sources["risk"] == "env"

    def test_env_weight_wins_even_without_a_source(self, monkeypatch) -> None:
        scenario = _materialize()
        monkeypatch.setenv("SCORE_WEIGHT_COST", "1.0")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["cost"] == 1.0
        assert sources["cost"] == "env"

    def test_resource_strain_is_opt_in_env_only(self, monkeypatch) -> None:
        scenario = _materialize()
        _weights, sources = resolve_weights(scenario, _options("b->a"))
        assert sources["resource_strain"] == "disabled_opt_in"
        monkeypatch.setenv("SCORE_WEIGHT_STRAIN", "0.75")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["resource_strain"] == 0.75
        assert sources["resource_strain"] == "env"

    def test_cost_auto_enables_when_rate_and_effort_are_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _materialize()
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["cost"] == 1.0
        assert sources["cost"] == "data"

    def test_cost_stays_off_when_any_affected_entity_lacks_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entities = [
            {"id": "a", "name": "A", "status": "in_progress", "effort": 2},
            {"id": "b", "name": "B", "status": "pending"},  # no effort, no metadata.cost
            {"id": "d", "name": "D", "status": "pending", "effort": 1},
        ]
        scenario = _materialize(entities)
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["cost"] == 0.0
        assert sources["cost"] == "disabled_incomplete_data"

    def test_cost_uses_declared_metadata_cost_when_present(self) -> None:
        entities = [
            {"id": "a", "name": "A", "status": "in_progress", "metadata": {"cost": 50}},
            {"id": "b", "name": "B", "status": "pending", "metadata": {"cost": 25}},
            {"id": "d", "name": "D", "status": "done"},  # done => skipped entirely
        ]
        scenario = _materialize(entities)
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["cost"] == 1.0
        assert sources["cost"] == "data"
        metrics = derive_metrics(scenario, _options("b->a"))
        assert metrics["drop-dependency:b->a"]["cost"] == 75.0

    def test_risk_auto_enables_once_incidents_exist(self) -> None:
        scenario = _materialize()
        _incident(scenario["scenario_id"], "b", "high", 0)
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["risk"] == 1.0
        assert sources["risk"] == "data"
        assert weights["disruption_count"] == 0.0
        assert sources["disruption_count"] == "disabled_no_source"

    def test_disruption_count_auto_enables_with_open_disruptions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rippleback.commit import record_disruption

        scenario = _materialize()
        monkeypatch.setenv("RIPPLEBACK_WRITES_ENABLED", "true")
        record_disruption(scenario["scenario_id"], entity_id="b", idempotency_key="m1")
        weights, sources = resolve_weights(scenario, _options("b->a"))
        assert weights["disruption_count"] == 1.0
        assert sources["disruption_count"] == "data"
        assert weights["risk"] == 1.0  # open disruptions also feed risk

    def test_incomplete_options_disable_a_metric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _materialize()
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        # second option can't be priced: it targets done entity c only
        options = [
            {"option_id": "drop-dependency:b->a", "changes": [DROP]},
            {
                "option_id": "drop-dependency:d->b",
                "changes": [
                    {"action": "remove_dependency", "dependent": "d", "prerequisite": "b"}
                ],
            },
        ]
        weights, sources = resolve_weights(scenario, options)
        assert weights["cost"] == 1.0
        assert sources["cost"] == "data"

    def test_empty_option_list_disables_default_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _materialize()
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        weights, sources = resolve_weights(scenario, [])
        assert weights["cost"] == 0.0
        assert sources["cost"] == "disabled_incomplete_data"

    def test_all_metric_keys_are_reported(self) -> None:
        scenario = _materialize()
        _weights, sources = resolve_weights(scenario, _options("b->a"))
        assert set(sources) == set(METRIC_KEYS)


# ── cost ──────────────────────────────────────────────────────────────────────


class TestDerivedCost:
    def test_cost_is_per_change_plus_rework(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _materialize()
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        monkeypatch.setenv("COST_PER_CHANGE", "100")
        metrics = derive_metrics(scenario, _options("b->a"))
        # affected unfinished: a (2) + b (3) + d (1) = 6 work units => 60, +100 per change
        assert metrics["drop-dependency:b->a"]["cost"] == 160.0

    def test_done_entities_are_not_reworked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entities = [
            {"id": "a", "name": "A", "status": "done", "effort": 2},
            {"id": "b", "name": "B", "status": "done", "effort": 3},
        ]
        scenario = _materialize(entities)
        monkeypatch.setenv("COST_RATE_PER_WORK_UNIT", "10")
        metrics = derive_metrics(scenario, _options("b->a"))
        assert metrics["drop-dependency:b->a"]["cost"] == 0.0

    def test_cost_is_omitted_when_not_computable(self) -> None:
        entities = [{"id": "a", "name": "A", "status": "in_progress"}]
        scenario = _materialize(entities)
        metrics = derive_metrics(scenario, _options("b->a"))
        assert "cost" not in metrics["drop-dependency:b->a"]

    def test_changes_without_a_matching_option_get_an_empty_metric_set(self) -> None:
        scenario = _materialize()
        metrics = derive_metrics(
            scenario, [{"option_id": "substitute:a->d", "label": "sub"}]
        )
        assert metrics["substitute:a->d"] == {}


# ── risk ──────────────────────────────────────────────────────────────────────


class TestDerivedRisk:
    def test_severity_weights_and_no_decay_at_reference_time(self) -> None:
        affected = {"b"}
        events = [("b", "critical", "2026-01-01T00:00:00+00:00")]
        assert derive_risk(affected, events) == 4.0

    def test_decay_halves_after_the_half_life(self) -> None:
        events = [
            ("b", "high", "2026-03-01T00:00:00+00:00"),
            ("b", "high", "2026-01-30T00:00:00+00:00"),  # 30 days earlier
        ]
        # reference = newest (03-01); older event decays by 0.5
        assert derive_risk({"b"}, events) == pytest.approx(3.0 + 1.5)

    def test_unknown_severity_defaults_to_one(self) -> None:
        assert derive_risk({"b"}, [("b", "wat", "2026-01-01T00:00:00+00:00")]) == 1.0

    def test_events_outside_the_blast_radius_do_not_count(self) -> None:
        events = [("c", "critical", "2026-01-01T00:00:00+00:00")]
        assert derive_risk({"b"}, events) == 0.0

    def test_no_events_means_no_risk_metric(self) -> None:
        assert derive_risk({"b"}, []) is None

    def test_unparseable_timestamps_do_not_crash(self) -> None:
        assert derive_risk({"b"}, [("b", "high", "not-a-date")]) is None

    def test_risk_is_deterministic_regardless_of_wall_clock(self) -> None:
        events = [
            ("b", "high", "2020-01-01T00:00:00+00:00"),
            ("b", "low", "2021-01-01T00:00:00+00:00"),
        ]
        assert derive_risk({"b"}, events) == derive_risk({"b"}, events)


# ── disruption count ──────────────────────────────────────────────────────────


class TestDerivedDisruptionCount:
    def test_counts_only_open_disruptions_in_radius(self) -> None:
        rows = [("b", "high", "t"), ("c", "low", "t")]
        assert derive_disruption_count({"a", "b", "d"}, rows) == 1

    def test_returns_none_when_there_are_no_open_disruptions(self) -> None:
        assert derive_disruption_count({"b"}, []) is None


# ── end-to-end derivation ─────────────────────────────────────────────────────


class TestDeriveMetrics:
    def test_metrics_cover_every_option(self) -> None:
        scenario = _materialize()
        _incident(scenario["scenario_id"], "b", "high", 0)
        metrics = derive_metrics(scenario, _options("b->a", "d->b"))
        assert set(metrics) == {"drop-dependency:b->a", "drop-dependency:d->b"}
        assert "risk" in metrics["drop-dependency:b->a"]

    def test_non_dict_options_are_skipped(self) -> None:
        scenario = _materialize()
        metrics = derive_metrics(scenario, ["nonsense", {"label": "no id"}])
        assert metrics == {}

    def test_option_without_changes_or_known_id_is_empty(self) -> None:
        scenario = _materialize()
        metrics = derive_metrics(scenario, [{"option_id": "substitute:a->b"}])
        assert metrics == {"substitute:a->b": {}}

