"""Milestone 6: MCP tool registration, wrapper contracts, and engine pipeline.

Three layers, all deterministic:

1. Static registration checks over ``src/rippleback/server.py`` (AST — no
   ``mcp`` import needed): the original six tools keep their exact names and
   signatures, the four Milestone 6 tools and the gated write tools are
   registered exactly once, and no engine module imports the MCP server.
2. Engine-level payment-API-outage pipeline on real fixtures: impact →
   recovery options → hard-constraint gate → forecast → comparison/ranking →
   substitutes, including invalid-graph, immutability, determinism, and
   JSON-serializability checks.
3. Server wrapper contracts (skipped when ``mcp`` is unavailable): ToolError
   mapping, in-band vs error outcomes, and substitute-id guidance.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from rippleback.comparison import compare_recovery_options
from rippleback.constraints import validate_hard_constraints
from rippleback.forecast import ForecastError, recalculate_delivery_forecast
from rippleback.impact import simulate_disruption
from rippleback.recovery import (
    find_alternative_dependencies,
    find_substitute_options,
)
from rippleback.scenarios import get_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "src" / "rippleback" / "server.py"
ENGINE_MODULES = (
    "forecast",
    "constraints",
    "scoring",
    "comparison",
    "recovery",
    "scheduler",
)

ORIGINAL_TOOLS = {
    "get_plan_state": ["scenario_id"],
    "simulate_disruption": ["scenario_id", "entity_id", "reason", "severity"],
    "find_alternative_dependencies": ["scenario_id", "entity_id"],
    "evaluate_recovery_plan": ["scenario_id", "option_id", "expected_plan_version"],
    "rebalance_workload": ["scenario_id", "expected_plan_version", "resource_id"],
    "analyze_blocked_tasks": [
        "scenario_id",
        "entity_id",
        "event_type",
        "reason",
        "delay_minutes",
    ],
}
NEW_TOOLS = {
    "recalculate_delivery_forecast": [
        "scenario_id",
        "changes",
        "option_id",
        "disrupted_entity_id",
    ],
    "validate_hard_constraints": ["scenario_id", "changes"],
    "compare_recovery_options": ["scenario_id", "options", "weights"],
    "find_substitute_options": ["scenario_id", "entity_id"],
    "generate_recovery_schedule": [
        "scenario_id",
        "changes",
        "option_id",
        "disrupted_entity_id",
        "scheduling_config",
    ],
}

# Read-only listing + the five gated write tools (production write path).
GATED_TOOLS = {
    "list_scenarios": [],
    "dry_run_recovery_plan": [
        "scenario_id",
        "expected_version",
        "option_id",
        "changes",
        "kind",
        "ctx",
    ],
    "record_disruption": [
        "scenario_id",
        "entity_id",
        "event_type",
        "reason",
        "severity",
        "delay_minutes",
        "idempotency_key",
        "actor",
        "ctx",
    ],
    "resolve_disruption": ["disruption_id", "idempotency_key", "actor", "ctx"],
    "commit_recovery_plan": [
        "scenario_id",
        "expected_version",
        "confirm_token",
        "option_id",
        "changes",
        "idempotency_key",
        "actor",
        "ctx",
    ],
    "commit_reassignment": [
        "scenario_id",
        "expected_version",
        "confirm_token",
        "changes",
        "idempotency_key",
        "actor",
        "ctx",
    ],
}


def _registered_tools() -> dict[str, list[str]]:
    """Map tool name -> parameter names for every ``@mcp.tool`` in server.py."""
    tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
    registered: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                continue
            name_kw = next(
                (
                    kw
                    for kw in decorator.keywords
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant)
                ),
                None,
            )
            tool_name = name_kw.value.value if name_kw else node.name
            registered.setdefault(tool_name, []).append(
                [arg.arg for arg in node.args.args]
            )
    return registered


class TestToolRegistration:
    def test_all_tools_registered_exactly_once(self) -> None:
        registered = _registered_tools()
        expected = set(ORIGINAL_TOOLS) | set(NEW_TOOLS) | set(GATED_TOOLS)
        assert set(registered) == expected
        assert all(len(calls) == 1 for calls in registered.values()), (
            "duplicate registration: "
            f"{ {name: len(calls) for name, calls in registered.items() if len(calls) != 1} }"
        )

    def test_original_six_signatures_unchanged(self) -> None:
        registered = _registered_tools()
        for name, params in ORIGINAL_TOOLS.items():
            assert registered[name] == [params], name

    def test_four_new_tool_signatures(self) -> None:
        registered = _registered_tools()
        for name, params in NEW_TOOLS.items():
            assert registered[name] == [params], name

    def test_gated_write_tool_signatures(self) -> None:
        registered = _registered_tools()
        for name, params in GATED_TOOLS.items():
            assert registered[name] == [params], name

    def test_engine_modules_do_not_import_mcp_or_the_server(self) -> None:
        for module in ENGINE_MODULES:
            source = (
                REPO_ROOT / "src" / "rippleback" / f"{module}.py"
            ).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert not any(
                        alias.name == "mcp" or alias.name.startswith("mcp.")
                        for alias in node.names
                    ), f"{module}.py imports mcp"
                elif isinstance(node, ast.ImportFrom):
                    assert node.module != "rippleback.server", (
                        f"{module}.py imports the MCP server"
                    )
                    assert not (
                        node.module
                        and (node.module == "mcp" or node.module.startswith("mcp."))
                    ), f"{module}.py imports mcp"


DEMO = "demo-release-plan"
PROJECT = "project-full"
DISRUPTED = "payment-api"
DROP_OPTION_ID = "drop-dependency:backend-integration->payment-api"
DROP_CHANGE = {
    "action": "remove_dependency",
    "dependent": "backend-integration",
    "prerequisite": "payment-api",
}
CYCLE_CHANGE = {
    "action": "add_dependency",
    "dependent": "payment-api",
    "prerequisite": "production-release",
}


def _pipeline(scenario_id: str) -> dict[str, str | dict]:
    """Run the full Milestone 3 phase-3 pipeline on a real fixture."""
    scenario = get_scenario(scenario_id)
    impact = simulate_disruption(
        scenario, entity_id=DISRUPTED, reason="service outage", severity="high"
    )
    alternatives = find_alternative_dependencies(scenario, entity_id=DISRUPTED)
    gate = validate_hard_constraints(scenario, changes=[DROP_CHANGE])
    forecast = recalculate_delivery_forecast(scenario, changes=[DROP_CHANGE])
    comparison = compare_recovery_options(scenario, alternatives["options"])
    substitutes = find_substitute_options(scenario, entity_id=DISRUPTED)
    return {
        "scenario": scenario,
        "impact": impact,
        "alternatives": alternatives,
        "gate": gate,
        "forecast": forecast,
        "comparison": comparison,
        "substitutes": substitutes,
    }


class TestEnginePipeline:
    def test_payment_api_outage_pipeline_demo_release_plan(self) -> None:
        results = _pipeline(DEMO)

        direct = [e["entity_id"] for e in results["impact"]["directly_affected"]]
        transitive = [
            e["entity_id"] for e in results["impact"]["transitively_affected"]
        ]
        assert direct == ["backend-integration"]
        assert transitive == [
            "integration-testing",
            "release-validation",
            "production-release",
        ]

        options = results["alternatives"]["options"]
        assert [option["option_id"] for option in options] == [DROP_OPTION_ID]

        assert results["gate"]["feasible"] is True
        assert results["gate"]["violations"] == []

        forecast = results["forecast"]
        assert forecast["feasible"] is True
        assert forecast["unit"] == "steps"
        assert forecast["baseline_length"] == 5.0
        assert forecast["forecast_length"] == 4.0
        assert forecast["delay_delta"] == -1.0

        comparison = results["comparison"]
        assert comparison["outcome"] == "ranked"
        assert comparison["recommendation"] == DROP_OPTION_ID
        ranked = comparison["ranked"][0]
        assert ranked["feasible"] is True
        assert ranked["forecast"]["delay_delta"] == -1.0

        substitutes = results["substitutes"]
        assert substitutes["outcome"] == "no_substitute_candidates"
        assert substitutes["options"] == []
        assert substitutes["missing_data"]

    def test_payment_api_outage_pipeline_project_full(self) -> None:
        results = _pipeline(PROJECT)
        assert results["gate"]["feasible"] is True
        assert results["forecast"]["feasible"] is True
        assert results["comparison"]["outcome"] == "ranked"
        assert results["comparison"]["recommendation"] == DROP_OPTION_ID
        assert results["substitutes"]["outcome"] == "no_substitute_candidates"

    def test_invalid_graph_is_never_feasible_anywhere(self) -> None:
        scenario = get_scenario(DEMO)

        gate = validate_hard_constraints(scenario, changes=[CYCLE_CHANGE])
        assert gate["feasible"] is False
        assert [v["code"] for v in gate["violations"]] == ["CYCLIC_DEPENDENCY"]

        forecast = recalculate_delivery_forecast(scenario, changes=[CYCLE_CHANGE])
        assert forecast["feasible"] is False
        assert forecast["error"]["code"] == "CYCLIC_DEPENDENCY"
        assert forecast["forecast_length"] is None
        assert forecast["delay_delta"] is None

        cycle_option = {
            "option_id": "invalid-cycle-option",
            "changes": [CYCLE_CHANGE],
        }
        comparison = compare_recovery_options(scenario, [cycle_option])
        assert comparison["outcome"] == "no_feasible_recovery"
        assert comparison["recommendation"] is None
        assert comparison["ranked"] == []
        assert comparison["excluded"][0]["feasible"] is False
        assert [v["code"] for v in comparison["excluded"][0]["violations"]] == [
            "CYCLIC_DEPENDENCY"
        ]

    def test_original_scenario_is_never_mutated(self) -> None:
        for scenario_id in (DEMO, PROJECT):
            pristine = copy.deepcopy(get_scenario(scenario_id))
            _pipeline(scenario_id)
            assert get_scenario(scenario_id) == pristine, scenario_id

    def test_all_pipeline_reports_are_json_serializable(self) -> None:
        for scenario_id in (DEMO, PROJECT):
            results = _pipeline(scenario_id)
            for key in (
                "impact",
                "alternatives",
                "gate",
                "forecast",
                "comparison",
                "substitutes",
            ):
                json.dumps(results[key])

    def test_pipeline_is_deterministic(self) -> None:
        first = _pipeline(DEMO)
        second = _pipeline(DEMO)
        for key in (
            "impact",
            "alternatives",
            "gate",
            "forecast",
            "comparison",
            "substitutes",
        ):
            assert first[key] == second[key], key

    def test_substitute_options_flow_through_explicit_changes(self) -> None:
        """Substitute ids are not parseable by the legacy id parser, so their
        explicit change sets are what forecast and comparison consume."""
        scenario = {
            "scenario_id": "synthetic-substitute-flow",
            "plan_id": "plan-synthetic-substitute-flow",
            "version": 7,
            "status": "healthy",
            "entities": [
                {
                    "id": "core",
                    "name": "Core",
                    "status": "in_progress",
                    "metadata": {"substitutes": ["backup-core"]},
                },
                {"id": "backup-core", "name": "Backup core", "status": "pending"},
                {"id": "alpha", "name": "Alpha", "status": "pending"},
            ],
            "dependencies": [{"dependent": "alpha", "prerequisite": "core"}],
            "constraints": [],
        }
        pristine = copy.deepcopy(scenario)

        options = find_substitute_options(scenario, entity_id="core")["options"]
        assert options and options[0]["option_id"] == "substitute:core->backup-core"

        with pytest.raises(ForecastError, match="drop-dependency"):
            recalculate_delivery_forecast(scenario, option_id=options[0]["option_id"])

        forecast = recalculate_delivery_forecast(scenario, changes=options[0]["changes"])
        assert forecast["feasible"] is True

        comparison = compare_recovery_options(scenario, options)
        assert comparison["outcome"] == "ranked"
        assert comparison["recommendation"] == options[0]["option_id"]
        assert comparison["ranked"][0]["feasible"] is True

        assert scenario == pristine


@pytest.fixture()
def server():
    """The MCP server module; the whole wrapper class skips without ``mcp``."""
    return pytest.importorskip("rippleback.server")


@pytest.fixture()
def tool_error(server):
    return server.ToolError


class TestServerWrapperContracts:
    """Direct wrapper calls; skipped when the ``mcp`` package is unavailable."""

    def test_original_six_wrappers_still_callable(self, server) -> None:
        plan = server.get_plan_state(DEMO)
        assert plan["scenario_id"] == DEMO

        impact = server.simulate_disruption(
            DEMO, entity_id=DISRUPTED, reason="service outage"
        )
        assert impact["simulated"] is True

        alternatives = server.find_alternative_dependencies(DEMO, entity_id=DISRUPTED)
        assert [o["option_id"] for o in alternatives["options"]] == [DROP_OPTION_ID]

        evaluation = server.evaluate_recovery_plan(
            DEMO, option_id=DROP_OPTION_ID, expected_plan_version=1
        )
        assert evaluation["verdict"] == "structurally_valid"

        rebalance = server.rebalance_workload("demo-release-plan-staffed", 1)
        assert rebalance["outcome"] == "ok"

        triage = server.analyze_blocked_tasks(PROJECT, entity_id=DISRUPTED)
        assert triage["simulated"] is True

    def test_forecast_valid_via_changes_and_option_id(self, server) -> None:
        via_changes = server.recalculate_delivery_forecast(DEMO, changes=[DROP_CHANGE])
        assert via_changes["feasible"] is True
        assert via_changes["delay_delta"] == -1.0

        via_id = server.recalculate_delivery_forecast(
            DEMO, option_id=DROP_OPTION_ID, disrupted_entity_id=DISRUPTED
        )
        assert via_id["feasible"] is True
        assert via_id["delay_delta"] == -1.0
        json.dumps(via_changes)
        json.dumps(via_id)

    def test_forecast_invalid_graph_is_in_band_not_an_error(
        self, server
    ) -> None:
        report = server.recalculate_delivery_forecast(DEMO, changes=[CYCLE_CHANGE])
        assert report["feasible"] is False
        assert report["error"]["code"] == "CYCLIC_DEPENDENCY"
        assert report["delay_delta"] is None

    def test_forecast_substitute_id_gets_explicit_changes_guidance(
        self, server, tool_error
    ) -> None:
        with pytest.raises(tool_error, match="explicit 'changes'"):
            server.recalculate_delivery_forecast(
                DEMO, option_id="substitute:payment-api->backup-api"
            )

    def test_forecast_malformed_inputs_are_tool_errors(
        self, server, tool_error
    ) -> None:
        with pytest.raises(tool_error, match="unknown scenario_id"):
            server.recalculate_delivery_forecast("no-such-scenario", changes=[])
        with pytest.raises(tool_error, match="changes must be a list"):
            server.recalculate_delivery_forecast(DEMO, changes="remove-me")
        with pytest.raises(tool_error, match="provide either"):
            server.recalculate_delivery_forecast(
                DEMO, changes=[DROP_CHANGE], option_id=DROP_OPTION_ID
            )
        with pytest.raises(tool_error, match="unknown disrupted_entity_id"):
            server.recalculate_delivery_forecast(
                DEMO, changes=[DROP_CHANGE], disrupted_entity_id="ghost"
            )

    def test_validate_constraints_valid_and_invalid(self, server, tool_error) -> None:
        baseline = server.validate_hard_constraints(DEMO)
        assert baseline["feasible"] is True
        assert baseline["violations"] == []

        infeasible = server.validate_hard_constraints(DEMO, changes=[CYCLE_CHANGE])
        assert infeasible["feasible"] is False
        assert [v["code"] for v in infeasible["violations"]] == [
            "CYCLIC_DEPENDENCY"
        ]
        json.dumps(baseline)
        json.dumps(infeasible)

        with pytest.raises(tool_error, match="unknown scenario_id"):
            server.validate_hard_constraints("no-such-scenario")
        with pytest.raises(tool_error, match="changes must be a list"):
            server.validate_hard_constraints(DEMO, changes=42)
        with pytest.raises(tool_error):
            server.validate_hard_constraints(
                DEMO, changes=[{"action": "teleport_dependency"}]
            )

    def test_compare_ranks_and_never_recommends_infeasible(
        self, server, tool_error
    ) -> None:
        alternatives = server.find_alternative_dependencies(DEMO, entity_id=DISRUPTED)
        options = [
            {"option_id": "invalid-cycle-option", "changes": [CYCLE_CHANGE]},
            *alternatives["options"],
        ]
        report = server.compare_recovery_options(DEMO, options)
        assert report["outcome"] == "ranked"
        assert report["recommendation"] == DROP_OPTION_ID
        assert report["excluded"][0]["option_id"] == "invalid-cycle-option"
        assert report["excluded"][0]["feasible"] is False
        json.dumps(report)

        only_bad = server.compare_recovery_options(
            DEMO, [{"option_id": "invalid-cycle-option", "changes": [CYCLE_CHANGE]}]
        )
        assert only_bad["outcome"] == "no_feasible_recovery"
        assert only_bad["recommendation"] is None

        with pytest.raises(tool_error, match="unknown scenario_id"):
            server.compare_recovery_options("no-such-scenario", options=[])
        with pytest.raises(tool_error, match="options must be a non-empty list"):
            server.compare_recovery_options(DEMO, options="not-a-list")
        with pytest.raises(tool_error):
            server.compare_recovery_options(DEMO, options=[{"option_id": ""}])
        with pytest.raises(tool_error, match="weight"):
            server.compare_recovery_options(
                DEMO, alternatives["options"], weights={"delay": -1.0}
            )

    def test_substitutes_no_candidate_and_error_paths(
        self, server, tool_error
    ) -> None:
        for scenario_id, entity_id in (
            (DEMO, DISRUPTED),
            (PROJECT, DISRUPTED),
            ("travel", "leg-1"),
        ):
            report = server.find_substitute_options(scenario_id, entity_id=entity_id)
            assert report["outcome"] == "no_substitute_candidates"
            assert report["options"] == []
            assert report["missing_data"]
            json.dumps(report)

        with pytest.raises(tool_error, match="unknown scenario_id"):
            server.find_substitute_options("no-such-scenario", entity_id=DISRUPTED)
        with pytest.raises(tool_error, match="unknown entity_id"):
            server.find_substitute_options(DEMO, entity_id="no-such-entity")

    def test_new_tools_do_not_mutate_the_committed_plan(self, server) -> None:
        pristine = copy.deepcopy(get_scenario(DEMO))
        server.recalculate_delivery_forecast(DEMO, changes=[DROP_CHANGE])
        server.validate_hard_constraints(DEMO, changes=[CYCLE_CHANGE])
        alternatives = server.find_alternative_dependencies(DEMO, entity_id=DISRUPTED)
        server.compare_recovery_options(DEMO, alternatives["options"])
        server.find_substitute_options(DEMO, entity_id=DISRUPTED)
        assert get_scenario(DEMO) == pristine
