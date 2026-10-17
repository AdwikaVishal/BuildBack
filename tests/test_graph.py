"""Unit tests for the typed dependency-graph builder and validation (Phase 6)."""

from __future__ import annotations

import copy

import pytest

from rippleback.graph import (
    EDGE_BENEFITS_FROM,
    EDGE_REQUIRES_ARRIVAL_BY,
    ERROR_CYCLIC_DEPENDENCY,
    ERROR_ENTITY_NOT_FOUND,
    ERROR_INVALID_DEPENDENCY,
    ERROR_INVALID_DEPENDENCY_TYPE,
    GraphError,
    build_graph,
    graph_errors,
    hard_successors,
    soft_successors,
)
from rippleback.scenarios import SCENARIOS, get_scenario


def _scenario() -> dict:
    return get_scenario("project-full")


class TestBuildGraph:
    def test_nodes_match_entities(self) -> None:
        scenario = _scenario()
        graph = build_graph(scenario)
        assert set(graph.nodes) == {entity["id"] for entity in scenario["entities"]}

    def test_default_edge_type_is_requires_completion(self) -> None:
        graph = build_graph(_scenario())
        edge = graph["payment-api"]["backend-integration"]
        assert edge["dependency_type"] == "requires_completion"
        assert edge["hard"] is True

    def test_benefits_from_is_soft(self) -> None:
        graph = build_graph(_scenario())
        edge = graph["integration-testing"]["unit-testing"]
        assert edge["dependency_type"] == EDGE_BENEFITS_FROM
        assert edge["hard"] is False

    def test_requires_arrival_by_is_hard(self) -> None:
        graph = build_graph(get_scenario("travel"))
        edge = graph["leg-1"]["transfer"]
        assert edge["dependency_type"] == EDGE_REQUIRES_ARRIVAL_BY
        assert edge["hard"] is True

    def test_node_attributes(self) -> None:
        graph = build_graph(_scenario())
        assert graph.nodes["vendor-delivery"]["external"] is True
        assert graph.nodes["release-notes"]["slack_minutes"] == 30
        assert graph.nodes["payment-api"]["status"] == "in_progress"

    def test_build_does_not_mutate_scenario(self) -> None:
        before = copy.deepcopy(SCENARIOS["project-full"])
        build_graph(_scenario())
        assert SCENARIOS["project-full"] == before


class TestSuccessors:
    def test_hard_successors_exclude_soft(self) -> None:
        graph = build_graph(_scenario())
        assert hard_successors(graph, "integration-testing") == ["release-validation"]

    def test_soft_successors(self) -> None:
        graph = build_graph(_scenario())
        assert soft_successors(graph, "integration-testing") == ["unit-testing"]


class TestValidationErrors:
    def _broken(self, mutate) -> dict:
        data = copy.deepcopy(SCENARIOS["project-full"])
        mutate(data)
        return data

    def test_unknown_reference(self) -> None:
        data = self._broken(
            lambda d: d["dependencies"].append(
                {"dependent": "ghost", "prerequisite": "payment-api"}
            )
        )
        errors = graph_errors(data)
        assert any(e["code"] == ERROR_ENTITY_NOT_FOUND for e in errors)
        with pytest.raises(GraphError) as excinfo:
            build_graph(data)
        assert excinfo.value.code == ERROR_ENTITY_NOT_FOUND

    def test_self_dependency(self) -> None:
        data = self._broken(
            lambda d: d["dependencies"].append(
                {"dependent": "payment-api", "prerequisite": "payment-api"}
            )
        )
        with pytest.raises(GraphError) as excinfo:
            build_graph(data)
        assert excinfo.value.code == ERROR_INVALID_DEPENDENCY

    def test_unknown_dependency_type(self) -> None:
        data = self._broken(
            lambda d: d["dependencies"].append(
                {
                    "dependent": "release-notes",
                    "prerequisite": "payment-api",
                    "dependency_type": "teleports_to",
                }
            )
        )
        with pytest.raises(GraphError) as excinfo:
            build_graph(data)
        assert excinfo.value.code == ERROR_INVALID_DEPENDENCY_TYPE

    def test_cycle_detected(self) -> None:
        data = self._broken(
            lambda d: d["dependencies"].append(
                {"dependent": "payment-api", "prerequisite": "production-release"}
            )
        )
        errors = graph_errors(data)
        assert any(e["code"] == ERROR_CYCLIC_DEPENDENCY for e in errors)
        with pytest.raises(GraphError) as excinfo:
            build_graph(data)
        assert excinfo.value.code == ERROR_CYCLIC_DEPENDENCY

    def test_graph_errors_empty_for_valid(self) -> None:
        assert graph_errors(_scenario()) == []

    def test_valid_acyclic_fixture_passes(self) -> None:
        build_graph(get_scenario("demo-release-plan"))
        build_graph(get_scenario("travel"))
