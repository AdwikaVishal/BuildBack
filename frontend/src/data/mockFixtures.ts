// ─────────────────────────────────────────────────────────────────────────────
// AUTO-GENERATED mock fixtures — captured from the live MCP tool outputs so the
// shapes match docs/data-contract.md exactly. Regenerate from captured JSON,
// do not hand-edit payload bodies.
// ─────────────────────────────────────────────────────────────────────────────

import type {
  Scenario,
  ImpactResult,
  TriageResult,
  RecoveryResult,
  ComparisonResult,
  EvaluationResult,
  ScheduleResult,
  MCPToolInfo,
} from '../types';

export const MOCK_PLAN = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "version": 1,
  "status": "healthy",
  "entities": [
    {
      "id": "auth-service",
      "name": "Auth service",
      "status": "done",
      "duration": 2,
      "assignee": "alice",
      "effort": 1
    },
    {
      "id": "payment-api",
      "name": "Payment API service",
      "status": "in_progress",
      "duration": 2,
      "assignee": "alice",
      "effort": 2
    },
    {
      "id": "vendor-delivery",
      "name": "Vendor deliverable",
      "status": "pending",
      "duration": 1,
      "assignee": "alice",
      "effort": 1,
      "metadata": {
        "external": true
      }
    },
    {
      "id": "backend-integration",
      "name": "Backend integration",
      "status": "pending",
      "duration": 3,
      "assignee": "alice",
      "effort": 2
    },
    {
      "id": "integration-testing",
      "name": "Integration testing",
      "status": "pending",
      "duration": 2,
      "assignee": "alice",
      "effort": 1
    },
    {
      "id": "release-validation",
      "name": "Release validation",
      "status": "pending",
      "duration": 1,
      "assignee": "alice",
      "effort": 3
    },
    {
      "id": "production-release",
      "name": "Production release",
      "status": "pending",
      "duration": 1,
      "assignee": "alice",
      "effort": 1
    },
    {
      "id": "frontend-integration",
      "name": "Frontend integration",
      "status": "in_progress",
      "duration": 2,
      "assignee": "bob",
      "effort": 2
    },
    {
      "id": "unit-testing",
      "name": "Unit testing",
      "status": "pending",
      "duration": 1,
      "assignee": "bob",
      "effort": 1
    },
    {
      "id": "release-notes",
      "name": "Release notes",
      "status": "pending",
      "duration": 1,
      "assignee": "bob",
      "effort": 1,
      "metadata": {
        "slack_minutes": 30
      }
    }
  ],
  "dependencies": [
    {
      "dependent": "backend-integration",
      "prerequisite": "payment-api"
    },
    {
      "dependent": "backend-integration",
      "prerequisite": "vendor-delivery"
    },
    {
      "dependent": "integration-testing",
      "prerequisite": "backend-integration"
    },
    {
      "dependent": "release-validation",
      "prerequisite": "integration-testing"
    },
    {
      "dependent": "production-release",
      "prerequisite": "release-validation"
    },
    {
      "dependent": "unit-testing",
      "prerequisite": "integration-testing",
      "dependency_type": "benefits_from"
    }
  ],
  "constraints": [
    {
      "type": "all_entities_done_before",
      "entity_ids": [
        "production-release"
      ],
      "description": "Every other entity must be done before production-release."
    }
  ],
  "disruptions": []
} as unknown as Scenario;

export const MOCK_IMPACT = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service",
    "state": "blocked",
    "reason": "Payment API service is down (service outage)",
    "severity": "high"
  },
  "directly_affected": [
    {
      "entity_id": "backend-integration",
      "name": "Backend integration",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration"
      ]
    }
  ],
  "transitively_affected": [
    {
      "entity_id": "integration-testing",
      "name": "Integration testing",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing"
      ]
    },
    {
      "entity_id": "release-validation",
      "name": "Release validation",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation"
      ]
    },
    {
      "entity_id": "production-release",
      "name": "Production release",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ]
    },
    {
      "entity_id": "unit-testing",
      "name": "Unit testing",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "unit-testing"
      ]
    }
  ],
  "unaffected": [
    {
      "entity_id": "auth-service",
      "name": "Auth service",
      "state": "done"
    },
    {
      "entity_id": "vendor-delivery",
      "name": "Vendor deliverable",
      "state": "pending"
    },
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration",
      "state": "in_progress"
    },
    {
      "entity_id": "release-notes",
      "name": "Release notes",
      "state": "pending"
    }
  ]
} as unknown as ImpactResult;

export const MOCK_TRIAGE = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service",
    "event_type": "service_outage",
    "reason": "Payment API service is down",
    "delay_minutes": null
  },
  "states": [
    {
      "entity_id": "auth-service",
      "name": "Auth service",
      "state": "done",
      "reason": "'auth-service' is already completed, so the disruption does not re-block it.",
      "evidence_path": [],
      "evidence_edges": []
    },
    {
      "entity_id": "payment-api",
      "name": "Payment API service",
      "state": "blocked",
      "reason": "'payment-api' is the disrupted entity; the service outage marks it unavailable.",
      "evidence_path": [
        "payment-api"
      ],
      "evidence_edges": []
    },
    {
      "entity_id": "vendor-delivery",
      "name": "Vendor deliverable",
      "state": "healthy",
      "reason": "No dependency path links 'vendor-delivery' to the disruption.",
      "evidence_path": [],
      "evidence_edges": []
    },
    {
      "entity_id": "backend-integration",
      "name": "Backend integration",
      "state": "blocked",
      "reason": "Hard dependency path from 'payment-api' reaches 'backend-integration' and no alternative satisfies it.",
      "evidence_path": [
        "payment-api",
        "backend-integration"
      ],
      "evidence_edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        }
      ]
    },
    {
      "entity_id": "integration-testing",
      "name": "Integration testing",
      "state": "blocked",
      "reason": "Hard dependency path from 'payment-api' reaches 'integration-testing' and no alternative satisfies it.",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing"
      ],
      "evidence_edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "integration-testing",
          "prerequisite": "backend-integration",
          "dependency_type": "requires_completion"
        }
      ]
    },
    {
      "entity_id": "release-validation",
      "name": "Release validation",
      "state": "blocked",
      "reason": "Hard dependency path from 'payment-api' reaches 'release-validation' and no alternative satisfies it.",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation"
      ],
      "evidence_edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "integration-testing",
          "prerequisite": "backend-integration",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "release-validation",
          "prerequisite": "integration-testing",
          "dependency_type": "requires_completion"
        }
      ]
    },
    {
      "entity_id": "production-release",
      "name": "Production release",
      "state": "blocked",
      "reason": "Hard dependency path from 'payment-api' reaches 'production-release' and no alternative satisfies it.",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ],
      "evidence_edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "integration-testing",
          "prerequisite": "backend-integration",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "release-validation",
          "prerequisite": "integration-testing",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "production-release",
          "prerequisite": "release-validation",
          "dependency_type": "requires_completion"
        }
      ]
    },
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration",
      "state": "healthy",
      "reason": "No dependency path links 'frontend-integration' to the disruption.",
      "evidence_path": [],
      "evidence_edges": []
    },
    {
      "entity_id": "unit-testing",
      "name": "Unit testing",
      "state": "at_risk",
      "reason": "'unit-testing' benefits_from 'integration-testing' but does not require it, so the disruption only adds risk.",
      "evidence_path": [
        "integration-testing",
        "unit-testing"
      ],
      "evidence_edges": [
        {
          "dependent": "unit-testing",
          "prerequisite": "integration-testing",
          "dependency_type": "benefits_from"
        }
      ]
    },
    {
      "entity_id": "release-notes",
      "name": "Release notes",
      "state": "at_risk",
      "reason": "'release-notes' has only 30 minute(s) of slack, at or below the 60-minute at-risk threshold.",
      "evidence_path": [
        "release-notes"
      ],
      "evidence_edges": []
    }
  ],
  "summary": {
    "done": 1,
    "blocked": 5,
    "external": 0,
    "at_risk": 2,
    "healthy": 2
  },
  "parallel_work": [
    {
      "entity_id": "vendor-delivery",
      "name": "Vendor deliverable"
    },
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration"
    },
    {
      "entity_id": "unit-testing",
      "name": "Unit testing"
    },
    {
      "entity_id": "release-notes",
      "name": "Release notes"
    }
  ],
  "critical_path": {
    "path": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "length": 9.0,
    "unit": "duration_units",
    "edges": [
      {
        "dependent": "backend-integration",
        "prerequisite": "payment-api",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "integration-testing",
        "prerequisite": "backend-integration",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "release-validation",
        "prerequisite": "integration-testing",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "production-release",
        "prerequisite": "release-validation",
        "dependency_type": "requires_completion"
      }
    ],
    "assumptions": [
      "Critical path follows hard requires edges only; benefits_from edges never define ordering."
    ],
    "limitations": [
      "No wall-clock dates are modeled; length is a relative ranking of dependency chains, not a delivery date or forecast."
    ]
  },
  "critical_path_change": {
    "baseline_path": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "baseline_length": 9.0,
    "unit": "duration_units",
    "disrupted_entity_id": "payment-api",
    "impacted_entity_ids": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "disrupted_path": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "disrupted_length": 9.0,
    "baseline_intersects_disruption": true,
    "disrupted_entity_is_external": false,
    "assumptions": [
      "The disruption affects exactly the entities reachable over hard requires edges (single-disruption model)."
    ],
    "limitations": [
      "Critical-path change is structural: it does not compute a new delivery date or the size of any delay."
    ]
  },
  "assumptions": [
    "Every entity is classified from the committed dependency graph for one disruption at a time.",
    "benefits_from edges add risk but never block.",
    "Hard reachability marks blockage; a 'done' entity is reported done and does not re-open."
  ],
  "limitations": [
    "The disrupted entity is classified blocked (or external) by this engine; its committed status is overridden by the event.",
    "Low-slack at-risk uses a fixed 60-minute threshold (open question).",
    "No recovery option is applied here; use recompute_with_changes to show the blocked-to-healthy payoff."
  ]
} as unknown as TriageResult;

export const MOCK_RECOVERY = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service"
  },
  "options": [
    {
      "option_id": "drop-dependency:backend-integration->payment-api",
      "description": "Bypass the dependency 'backend-integration' requires 'payment-api' so 'backend-integration' can proceed without the disrupted prerequisite.",
      "affected_entity_id": "backend-integration",
      "affected_dependency": {
        "dependent": "backend-integration",
        "prerequisite": "payment-api"
      },
      "alternative": {
        "action": "remove_dependency",
        "removed_dependency": {
          "dependent": "backend-integration",
          "prerequisite": "payment-api"
        },
        "dependencies": [
          {
            "dependent": "backend-integration",
            "prerequisite": "vendor-delivery"
          },
          {
            "dependent": "integration-testing",
            "prerequisite": "backend-integration"
          },
          {
            "dependent": "release-validation",
            "prerequisite": "integration-testing"
          },
          {
            "dependent": "production-release",
            "prerequisite": "release-validation"
          },
          {
            "dependent": "unit-testing",
            "prerequisite": "integration-testing",
            "dependency_type": "benefits_from"
          }
        ]
      },
      "unblocks": [
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release",
        "unit-testing"
      ],
      "assumptions": [
        "Assumes 'backend-integration' does not strictly require 'payment-api' for this release.",
        "Assumes the removed prerequisite can be satisfied out-of-band or is no longer needed."
      ],
      "limitations": [
        "Hypothetical only; the committed plan is not changed.",
        "Removing a hard prerequisite may break integration guarantees for 'backend-integration'.",
        "Requires human review before adoption."
      ]
    }
  ]
} as unknown as RecoveryResult;

export const MOCK_COMPARISON = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "outcome": "ranked",
  "weights": {
    "delay": 1.0,
    "cost": 0.0,
    "risk": 0.0,
    "resource_strain": 0.0,
    "disruption_count": 0.0
  },
  "feasible_count": 1,
  "scorable_count": 1,
  "recommendation": "drop-dependency:backend-integration->payment-api",
  "baseline": {
    "path": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "length": 9.0,
    "unit": "duration_units",
    "edges": [
      {
        "dependent": "backend-integration",
        "prerequisite": "payment-api",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "integration-testing",
        "prerequisite": "backend-integration",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "release-validation",
        "prerequisite": "integration-testing",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "production-release",
        "prerequisite": "release-validation",
        "dependency_type": "requires_completion"
      }
    ],
    "assumptions": [
      "Critical path follows hard requires edges only; benefits_from edges never define ordering."
    ],
    "limitations": [
      "No wall-clock dates are modeled; length is a relative ranking of dependency chains, not a delivery date or forecast."
    ]
  },
  "ranked": [
    {
      "option_id": "drop-dependency:backend-integration->payment-api",
      "rank": 1,
      "feasible": true,
      "scorable": true,
      "score": -1.0,
      "components": {
        "delay": -1.0,
        "cost": 0.0,
        "risk": 0.0,
        "resource_strain": 0.0,
        "disruption_count": 0.0
      },
      "metrics": {
        "delay": -1.0,
        "resource_strain": 8.0
      },
      "missing_metrics": [
        "cost",
        "risk",
        "disruption_count"
      ],
      "violations": [],
      "reasons": [],
      "forecast": {
        "scenario_id": "project-full",
        "plan_id": "plan-payment-api-release-full",
        "plan_version": 1,
        "simulated": true,
        "disrupted_entity_id": null,
        "option_id": null,
        "changes": [
          {
            "action": "remove_dependency",
            "dependent": "backend-integration",
            "prerequisite": "payment-api"
          }
        ],
        "feasible": true,
        "error": null,
        "unit": "duration_units",
        "baseline_path": [
          "payment-api",
          "backend-integration",
          "integration-testing",
          "release-validation",
          "production-release"
        ],
        "baseline_length": 9.0,
        "baseline_edges": [
          {
            "dependent": "backend-integration",
            "prerequisite": "payment-api",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "integration-testing",
            "prerequisite": "backend-integration",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "release-validation",
            "prerequisite": "integration-testing",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "production-release",
            "prerequisite": "release-validation",
            "dependency_type": "requires_completion"
          }
        ],
        "forecast_path": [
          "vendor-delivery",
          "backend-integration",
          "integration-testing",
          "release-validation",
          "production-release"
        ],
        "forecast_length": 8.0,
        "forecast_edges": [
          {
            "dependent": "backend-integration",
            "prerequisite": "vendor-delivery",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "integration-testing",
            "prerequisite": "backend-integration",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "release-validation",
            "prerequisite": "integration-testing",
            "dependency_type": "requires_completion"
          },
          {
            "dependent": "production-release",
            "prerequisite": "release-validation",
            "dependency_type": "requires_completion"
          }
        ],
        "delay_delta": -1.0,
        "assumptions": [
          "Baseline is the committed plan's critical path; the forecast is the critical path after applying the change set to a copy of the plan. The committed plan, its statuses, and its version are never modified.",
          "'delay_delta' compares total critical-path duration before and after the change set; it is not delay relative to a disrupted baseline and not a calendar delivery date.",
          "Critical path follows hard requires edges only; benefits_from edges never define ordering.",
          "Changes are applied with rippleback.triage.apply_changes semantics; removing an edge that does not exist is a no-op."
        ],
        "limitations": [
          "No wall-clock dates are modeled: lengths are relative critical-path durations in the reported unit, never calendar delivery dates.",
          "The forecast reflects only the proposed change set; disruption state, resource capacity, workload, and slack are not modeled.",
          "delay_delta is a whole-plan critical-path comparison; it does not measure per-task delay or delivery-date risk."
        ]
      },
      "no_op": false,
      "assumptions": [
        "Baseline is the committed plan's critical path; the forecast is the critical path after applying the change set to a copy of the plan. The committed plan, its statuses, and its version are never modified.",
        "'delay_delta' compares total critical-path duration before and after the change set; it is not delay relative to a disrupted baseline and not a calendar delivery date.",
        "Critical path follows hard requires edges only; benefits_from edges never define ordering.",
        "Changes are applied with rippleback.triage.apply_changes semantics; removing an edge that does not exist is a no-op."
      ],
      "limitations": [
        "No wall-clock dates are modeled: lengths are relative critical-path durations in the reported unit, never calendar delivery dates.",
        "The forecast reflects only the proposed change set; disruption state, resource capacity, workload, and slack are not modeled.",
        "delay_delta is a whole-plan critical-path comparison; it does not measure per-task delay or delivery-date risk."
      ]
    }
  ],
  "excluded": [],
  "assumptions": [
    "Score = w1*delay + w2*cost + w3*risk + w4*resource_strain + w5*disruption_count over the echoed weights; lower is better and equal scores tie-break by ascending option_id.",
    "Feasibility is gated with rippleback.constraints.validate_hard_constraints before scoring; infeasible options keep their violations, are never scored, and never appear in the recommendation.",
    "delay derives from the forecast delay_delta (relative critical-path units) and resource_strain from workload overload (work_units); caller-supplied metrics override derived values.",
    "cost, risk, and disruption_count have no source in the repository; their default weight is 0.0 and they contribute only when a caller supplies both a value and a nonzero weight.",
    "Weights are unitless; scores are comparable only within one call.",
    "'baseline' is recomputed from the committed plan with rippleback.critical_path; its length is a total duration, while each entry's forecast delay_delta is the change that option's changes make to that total.",
    "Every ranked and excluded entry carries a forecast recomputed from the same scenario and that option's change set with rippleback.forecast.recalculate_delivery_forecast; forecasts carry no disruption context (disrupted_entity_id is None) and reuse the option's changes rather than its id.",
    "'no_op' is true exactly when applying the option's change set yields the committed dependency plan unchanged (edge order ignored); no-op options keep delay_delta 0.0 and are limited so they are never presented as improvements.",
    "The report never modifies the committed plan, the options, or the weights; recovery-option ids and evaluation verdicts are unchanged."
  ],
  "limitations": [
    "The repository supplies no cost or risk data; defaults zero those weights instead of fabricating values, and a nonzero weight without data yields an unscorable option, never a zero-filled score.",
    "resource_strain is scenario-level (identical for every option -- workload ignores dependencies) and is absent for scenarios without staffing data.",
    "No delivery date is predicted: delay is a relative critical-path delta under a single-disruption model, never wall-clock time.",
    "Read-only: options, the committed plan, evaluation verdicts, and recovery-option ids are unchanged.",
    "'baseline' reports the committed plan's total critical-path duration in 'unit'; each entry's forecast delay_delta is a difference of totals (forecast_length - baseline_length), not a duration and not a delay relative to a disruption.",
    "Lengths are relative critical-path durations (steps or duration_units), never wall-clock dates, and are not comparable across scenarios whose units differ.",
    "forecast.feasible says the option's changes yield a valid dependency graph; the entry's feasible flag (from rippleback.constraints) is what gates ranking, and an entry must be feasible and scorable to be recommended."
  ]
} as unknown as ComparisonResult;

export const MOCK_EVALUATION = {
  "evaluation_id": "eval:project-full:drop-dependency:backend-integration->payment-api:v1",
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "expected_plan_version": 1,
  "verdict": "structurally_valid",
  "dependency": {
    "dependent": "backend-integration",
    "prerequisite": "payment-api"
  },
  "findings": [
    {
      "code": "plan_version_match",
      "level": "info",
      "message": "expected_plan_version 1 matches the committed plan version 1."
    },
    {
      "code": "dependency_edge_exists",
      "level": "info",
      "message": "dependency edge 'backend-integration' requires 'payment-api' exists in the committed plan."
    },
    {
      "code": "business_readiness_not_assessed",
      "level": "warning",
      "message": "Structural validity only: business readiness (integration contracts, substitute services, delivery constraints) is not assessed and must be reviewed by a human."
    }
  ],
  "impact": {
    "disrupted_entity_id": "payment-api",
    "removed_dependency": {
      "dependent": "backend-integration",
      "prerequisite": "payment-api"
    },
    "unblocks": [
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release",
      "unit-testing"
    ],
    "still_blocked": []
  },
  "assumptions": [
    "Assumes 'payment-api' is the only unavailable entity (single-disruption model).",
    "Assumes the committed dependency graph is authoritative for dependency ordering."
  ],
  "limitations": [
    "Structural validity does not imply business readiness; removing a hard prerequisite may break integration guarantees.",
    "No substitute candidates, cost/effort ranking, or scheduling are evaluated.",
    "Read-only: the committed plan is not changed and no recovery is committed."
  ]
} as unknown as EvaluationResult;

export const MOCK_SCHEDULE = {
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "disrupted_entity_id": "payment-api",
  "option_id": "drop-dependency:backend-integration->payment-api",
  "changes": [
    {
      "action": "remove_dependency",
      "dependent": "backend-integration",
      "prerequisite": "payment-api"
    }
  ],
  "feasible": true,
  "outcome": "scheduled",
  "error": null,
  "unit": "duration_units",
  "schedule": [
    {
      "entity_id": "auth-service",
      "name": "Auth service",
      "status": "done",
      "triage_state": "done",
      "group": "completed",
      "layer": 0,
      "duration": 2.0,
      "earliest_start": 0.0,
      "earliest_finish": 0.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [],
      "soft_prerequisites": [],
      "on_critical_path": false,
      "reason": "'auth-service' is already completed (status 'done'); it is complete at the relative origin and is never restarted."
    },
    {
      "entity_id": "payment-api",
      "name": "Payment API service",
      "status": "in_progress",
      "triage_state": "blocked",
      "group": "blocked",
      "layer": null,
      "duration": 2.0,
      "earliest_start": null,
      "earliest_finish": null,
      "waiting_time": null,
      "hard_prerequisites": [],
      "soft_prerequisites": [],
      "on_critical_path": false,
      "reason": "'payment-api' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
    },
    {
      "entity_id": "vendor-delivery",
      "name": "Vendor deliverable",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 1,
      "duration": 1.0,
      "earliest_start": 0.0,
      "earliest_finish": 1.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [],
      "soft_prerequisites": [],
      "on_critical_path": true,
      "reason": "'vendor-delivery' has no hard prerequisites and is ready at the relative origin."
    },
    {
      "entity_id": "backend-integration",
      "name": "Backend integration",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 2,
      "duration": 3.0,
      "earliest_start": 1.0,
      "earliest_finish": 4.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [
        "vendor-delivery"
      ],
      "soft_prerequisites": [],
      "on_critical_path": true,
      "reason": "'backend-integration' starts when its hard prerequisites finish (waiting 0 duration_units, executing 3 duration_units)."
    },
    {
      "entity_id": "integration-testing",
      "name": "Integration testing",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 3,
      "duration": 2.0,
      "earliest_start": 4.0,
      "earliest_finish": 6.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [
        "backend-integration"
      ],
      "soft_prerequisites": [],
      "on_critical_path": true,
      "reason": "'integration-testing' starts when its hard prerequisites finish (waiting 0 duration_units, executing 2 duration_units)."
    },
    {
      "entity_id": "release-validation",
      "name": "Release validation",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 4,
      "duration": 1.0,
      "earliest_start": 6.0,
      "earliest_finish": 7.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [
        "integration-testing"
      ],
      "soft_prerequisites": [],
      "on_critical_path": true,
      "reason": "'release-validation' starts when its hard prerequisites finish (waiting 0 duration_units, executing 1 duration_units)."
    },
    {
      "entity_id": "production-release",
      "name": "Production release",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 5,
      "duration": 1.0,
      "earliest_start": 7.0,
      "earliest_finish": 8.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [
        "release-validation"
      ],
      "soft_prerequisites": [],
      "on_critical_path": true,
      "reason": "'production-release' starts when its hard prerequisites finish (waiting 0 duration_units, executing 1 duration_units)."
    },
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration",
      "status": "in_progress",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 1,
      "duration": 2.0,
      "earliest_start": 0.0,
      "earliest_finish": 2.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [],
      "soft_prerequisites": [],
      "on_critical_path": false,
      "reason": "'frontend-integration' has no hard prerequisites and is ready at the relative origin."
    },
    {
      "entity_id": "unit-testing",
      "name": "Unit testing",
      "status": "pending",
      "triage_state": "healthy",
      "group": "ready_now",
      "layer": 1,
      "duration": 1.0,
      "earliest_start": 0.0,
      "earliest_finish": 1.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [],
      "soft_prerequisites": [
        "integration-testing"
      ],
      "on_critical_path": false,
      "reason": "'unit-testing' has no hard prerequisites and is ready at the relative origin."
    },
    {
      "entity_id": "release-notes",
      "name": "Release notes",
      "status": "pending",
      "triage_state": "at_risk",
      "group": "ready_now",
      "layer": 1,
      "duration": 1.0,
      "earliest_start": 0.0,
      "earliest_finish": 1.0,
      "waiting_time": 0.0,
      "hard_prerequisites": [],
      "soft_prerequisites": [],
      "on_critical_path": false,
      "reason": "'release-notes' has no hard prerequisites and is ready at the relative origin."
    }
  ],
  "groups": {
    "completed": [
      "auth-service"
    ],
    "ready_now": [
      "vendor-delivery",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release",
      "frontend-integration",
      "unit-testing",
      "release-notes"
    ],
    "waiting": [],
    "blocked": [
      "payment-api"
    ]
  },
  "execution_order": [
    "vendor-delivery",
    "frontend-integration",
    "unit-testing",
    "release-notes",
    "backend-integration",
    "integration-testing",
    "release-validation",
    "production-release"
  ],
  "parallel_groups": [
    {
      "layer": 1,
      "entity_ids": [
        "vendor-delivery",
        "frontend-integration",
        "unit-testing",
        "release-notes"
      ]
    },
    {
      "layer": 2,
      "entity_ids": [
        "backend-integration"
      ]
    },
    {
      "layer": 3,
      "entity_ids": [
        "integration-testing"
      ]
    },
    {
      "layer": 4,
      "entity_ids": [
        "release-validation"
      ]
    },
    {
      "layer": 5,
      "entity_ids": [
        "production-release"
      ]
    }
  ],
  "critical_path": {
    "path": [
      "vendor-delivery",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "length": 8.0,
    "unit": "duration_units",
    "edges": [
      {
        "dependent": "backend-integration",
        "prerequisite": "vendor-delivery",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "integration-testing",
        "prerequisite": "backend-integration",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "release-validation",
        "prerequisite": "integration-testing",
        "dependency_type": "requires_completion"
      },
      {
        "dependent": "production-release",
        "prerequisite": "release-validation",
        "dependency_type": "requires_completion"
      }
    ],
    "assumptions": [
      "Critical path follows hard requires edges only; benefits_from edges never define ordering."
    ],
    "limitations": [
      "No wall-clock dates are modeled; length is a relative ranking of dependency chains, not a delivery date or forecast."
    ]
  },
  "completion": {
    "duration": 8.0,
    "unit": "duration_units",
    "covers_all_tasks": false,
    "excluded_tasks": [
      "payment-api"
    ],
    "explanation": "Projected makespan over schedulable remaining work (relative units from the moment of analysis)."
  },
  "baseline": {
    "feasible": true,
    "violations": [],
    "unit": "duration_units",
    "schedule": [
      {
        "entity_id": "auth-service",
        "name": "Auth service",
        "status": "done",
        "triage_state": "done",
        "group": "completed",
        "layer": 0,
        "duration": 2.0,
        "earliest_start": 0.0,
        "earliest_finish": 0.0,
        "waiting_time": 0.0,
        "hard_prerequisites": [],
        "soft_prerequisites": [],
        "on_critical_path": false,
        "reason": "'auth-service' is already completed (status 'done'); it is complete at the relative origin and is never restarted."
      },
      {
        "entity_id": "payment-api",
        "name": "Payment API service",
        "status": "in_progress",
        "triage_state": "blocked",
        "group": "blocked",
        "layer": null,
        "duration": 2.0,
        "earliest_start": null,
        "earliest_finish": null,
        "waiting_time": null,
        "hard_prerequisites": [],
        "soft_prerequisites": [],
        "on_critical_path": true,
        "reason": "'payment-api' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
      },
      {
        "entity_id": "vendor-delivery",
        "name": "Vendor deliverable",
        "status": "pending",
        "triage_state": "healthy",
        "group": "ready_now",
        "layer": 1,
        "duration": 1.0,
        "earliest_start": 0.0,
        "earliest_finish": 1.0,
        "waiting_time": 0.0,
        "hard_prerequisites": [],
        "soft_prerequisites": [],
        "on_critical_path": false,
        "reason": "'vendor-delivery' has no hard prerequisites and is ready at the relative origin."
      },
      {
        "entity_id": "backend-integration",
        "name": "Backend integration",
        "status": "pending",
        "triage_state": "blocked",
        "group": "blocked",
        "layer": null,
        "duration": 3.0,
        "earliest_start": null,
        "earliest_finish": null,
        "waiting_time": null,
        "hard_prerequisites": [
          "payment-api",
          "vendor-delivery"
        ],
        "soft_prerequisites": [],
        "on_critical_path": true,
        "reason": "'backend-integration' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
      },
      {
        "entity_id": "integration-testing",
        "name": "Integration testing",
        "status": "pending",
        "triage_state": "blocked",
        "group": "blocked",
        "layer": null,
        "duration": 2.0,
        "earliest_start": null,
        "earliest_finish": null,
        "waiting_time": null,
        "hard_prerequisites": [
          "backend-integration"
        ],
        "soft_prerequisites": [],
        "on_critical_path": true,
        "reason": "'integration-testing' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
      },
      {
        "entity_id": "release-validation",
        "name": "Release validation",
        "status": "pending",
        "triage_state": "blocked",
        "group": "blocked",
        "layer": null,
        "duration": 1.0,
        "earliest_start": null,
        "earliest_finish": null,
        "waiting_time": null,
        "hard_prerequisites": [
          "integration-testing"
        ],
        "soft_prerequisites": [],
        "on_critical_path": true,
        "reason": "'release-validation' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
      },
      {
        "entity_id": "production-release",
        "name": "Production release",
        "status": "pending",
        "triage_state": "blocked",
        "group": "blocked",
        "layer": null,
        "duration": 1.0,
        "earliest_start": null,
        "earliest_finish": null,
        "waiting_time": null,
        "hard_prerequisites": [
          "release-validation"
        ],
        "soft_prerequisites": [],
        "on_critical_path": true,
        "reason": "'production-release' is blocked after the disruption (blocker-triage state), so it is not scheduled as executable."
      },
      {
        "entity_id": "frontend-integration",
        "name": "Frontend integration",
        "status": "in_progress",
        "triage_state": "healthy",
        "group": "ready_now",
        "layer": 1,
        "duration": 2.0,
        "earliest_start": 0.0,
        "earliest_finish": 2.0,
        "waiting_time": 0.0,
        "hard_prerequisites": [],
        "soft_prerequisites": [],
        "on_critical_path": false,
        "reason": "'frontend-integration' has no hard prerequisites and is ready at the relative origin."
      },
      {
        "entity_id": "unit-testing",
        "name": "Unit testing",
        "status": "pending",
        "triage_state": "at_risk",
        "group": "ready_now",
        "layer": 1,
        "duration": 1.0,
        "earliest_start": 0.0,
        "earliest_finish": 1.0,
        "waiting_time": 0.0,
        "hard_prerequisites": [],
        "soft_prerequisites": [
          "integration-testing"
        ],
        "on_critical_path": false,
        "reason": "'unit-testing' has no hard prerequisites and is ready at the relative origin."
      },
      {
        "entity_id": "release-notes",
        "name": "Release notes",
        "status": "pending",
        "triage_state": "at_risk",
        "group": "ready_now",
        "layer": 1,
        "duration": 1.0,
        "earliest_start": 0.0,
        "earliest_finish": 1.0,
        "waiting_time": 0.0,
        "hard_prerequisites": [],
        "soft_prerequisites": [],
        "on_critical_path": false,
        "reason": "'release-notes' has no hard prerequisites and is ready at the relative origin."
      }
    ],
    "groups": {
      "completed": [
        "auth-service"
      ],
      "ready_now": [
        "vendor-delivery",
        "frontend-integration",
        "unit-testing",
        "release-notes"
      ],
      "waiting": [],
      "blocked": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ]
    },
    "execution_order": [
      "vendor-delivery",
      "frontend-integration",
      "unit-testing",
      "release-notes"
    ],
    "parallel_groups": [
      {
        "layer": 1,
        "entity_ids": [
          "vendor-delivery",
          "frontend-integration",
          "unit-testing",
          "release-notes"
        ]
      }
    ],
    "critical_path": {
      "path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ],
      "length": 9.0,
      "unit": "duration_units",
      "edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "integration-testing",
          "prerequisite": "backend-integration",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "release-validation",
          "prerequisite": "integration-testing",
          "dependency_type": "requires_completion"
        },
        {
          "dependent": "production-release",
          "prerequisite": "release-validation",
          "dependency_type": "requires_completion"
        }
      ],
      "assumptions": [
        "Critical path follows hard requires edges only; benefits_from edges never define ordering."
      ],
      "limitations": [
        "No wall-clock dates are modeled; length is a relative ranking of dependency chains, not a delivery date or forecast."
      ]
    },
    "completion": {
      "duration": 2.0,
      "unit": "duration_units",
      "covers_all_tasks": false,
      "excluded_tasks": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ],
      "explanation": "Projected makespan over schedulable remaining work (relative units from the moment of analysis)."
    }
  },
  "comparison": {
    "comparable": true,
    "incomparable_reason": null,
    "baseline_unit": "duration_units",
    "proposed_unit": "duration_units",
    "baseline_execution_order": [
      "vendor-delivery",
      "frontend-integration",
      "unit-testing",
      "release-notes"
    ],
    "proposed_execution_order": [
      "vendor-delivery",
      "frontend-integration",
      "unit-testing",
      "release-notes",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "execution_order_changed": true,
    "baseline_critical_path": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "proposed_critical_path": [
      "vendor-delivery",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "critical_path_changed": true,
    "baseline_completion_duration": 2.0,
    "proposed_completion_duration": 8.0,
    "completion_delta": 6.0,
    "baseline_excluded_tasks": [
      "payment-api",
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "proposed_excluded_tasks": [
      "payment-api"
    ],
    "excluded_tasks_changed": true,
    "task_offset_changes": [
      {
        "entity_id": "backend-integration",
        "baseline_group": "blocked",
        "proposed_group": "ready_now",
        "baseline_start": null,
        "proposed_start": 1.0,
        "delta_start": null,
        "baseline_finish": null,
        "proposed_finish": 4.0,
        "delta_finish": null
      },
      {
        "entity_id": "integration-testing",
        "baseline_group": "blocked",
        "proposed_group": "ready_now",
        "baseline_start": null,
        "proposed_start": 4.0,
        "delta_start": null,
        "baseline_finish": null,
        "proposed_finish": 6.0,
        "delta_finish": null
      },
      {
        "entity_id": "release-validation",
        "baseline_group": "blocked",
        "proposed_group": "ready_now",
        "baseline_start": null,
        "proposed_start": 6.0,
        "delta_start": null,
        "baseline_finish": null,
        "proposed_finish": 7.0,
        "delta_finish": null
      },
      {
        "entity_id": "production-release",
        "baseline_group": "blocked",
        "proposed_group": "ready_now",
        "baseline_start": null,
        "proposed_start": 7.0,
        "delta_start": null,
        "baseline_finish": null,
        "proposed_finish": 8.0,
        "delta_finish": null
      }
    ],
    "newly_blocked": [],
    "newly_unblocked": [
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "parallelism": {
      "baseline_layer_count": 1,
      "proposed_layer_count": 5,
      "baseline_max_group_size": 4,
      "proposed_max_group_size": 4
    }
  },
  "violations": [],
  "missing_data": [],
  "assumptions": [
    "The schedule is relative: t=0 is the moment of analysis. The repository models no calendar, project start date, or working hours, so all times are relative offsets in the reported unit, never wall-clock dates.",
    "Ordering follows hard requires edges only (requires_completion / requires_arrival_by); benefits_from edges are reported as soft prerequisites and never constrain start times.",
    "A task's earliest start is the maximum earliest finish of its hard prerequisites (+ any honored arrival buffer); tasks with no unfinished hard prerequisites are ready at the relative origin. The topological order is one valid order with deterministic lexicographic tie-breaking, not the only or optimal schedule.",
    "Committed 'done' tasks are already complete at t=0 and are never restarted or re-blocked; committed 'blocked' tasks are never scheduled as executable. 'in_progress' tasks are scheduled with their full declared duration from t=0; partial progress is not modeled.",
    "Change sets (when given) are applied to a copy of the plan with rippleback.triage.apply_changes semantics; the committed plan, statuses, and version are never modified.",
    "Readiness after the disruption on 'payment-api' uses blocker triage (default service_outage event): blocked/external entities are not scheduled, healthy/at_risk entities are. A recovery that removes the blocking path is recomputed against the modified graph — a hypothetical result, distinct from the committed state."
  ],
  "limitations": [
    "No calendar scheduling: no project start date, timezone, working days, working hours, or holidays exist in the data, so calendar dates cannot be derived and deadline optimization or resource leveling is not claimed.",
    "No resource-constrained scheduling: workload 'work_units' are abstract capacity with no time dimension, and the data models no skills, availability, or assignments-as-schedule; parallel groups assume unlimited parallel execution subject only to hard dependencies.",
    "The critical path shown is the existing structural longest-hard-chain analysis: it counts declared durations of completed tasks as well and is not a time forecast; the completion duration covers only unfinished, schedulable work.",
    "Blocked tasks have no scheduled finish; the completion duration covers only schedulable work (see completion.covers_all_tasks).",
    "Read-only and hypothetical: the committed plan is never modified, no schedule is committed, and business readiness of a recovery change is not assessed here.",
    "The baseline and proposed schedules exclude different tasks (their blocked/waiting sets differ), so completion_delta compares the makespan of the schedulable work on each side, not an identical work set; read it together with baseline_excluded_tasks / proposed_excluded_tasks."
  ]
} as unknown as ScheduleResult;

export const MOCK_TOOLS = [
  {
    "name": "get_plan_state",
    "description": "Return the current state of a seeded project scenario by scenario id: plan id, version, health status, entities, directed dependencies, and constraints. Unknown scenario ids raise a structured error. No impact analysis or recovery options in this phase.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        }
      },
      "required": [
        "scenario_id"
      ],
      "type": "object",
      "title": "get_plan_stateArguments"
    }
  },
  {
    "name": "simulate_disruption",
    "description": "Hypothetically disrupt one entity in a seeded scenario and return the dependency impact: directly affected, transitively affected, and unaffected entities, each with an evidence path. This is a simulation only — the committed plan is not modified and its version is unchanged. Unknown scenario/entity ids and invalid details return a structured error.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id",
          "type": "string"
        },
        "reason": {
          "title": "Reason",
          "type": "string"
        },
        "severity": {
          "default": "high",
          "title": "Severity",
          "type": "string"
        }
      },
      "required": [
        "scenario_id",
        "entity_id",
        "reason"
      ],
      "type": "object",
      "title": "simulate_disruptionArguments"
    }
  },
  {
    "name": "find_alternative_dependencies",
    "description": "Given a disrupted entity in a seeded scenario, return deterministic hypothetical recovery alternatives: for each dependency that requires the disrupted entity, an option proposing to bypass that dependency, with a stable id, description, affected dependency, assumptions, and limitations. Suggestions only — the committed plan is not modified. Unknown scenario/entity ids return a structured error.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id",
          "type": "string"
        }
      },
      "required": [
        "scenario_id",
        "entity_id"
      ],
      "type": "object",
      "title": "find_alternative_dependenciesArguments"
    }
  },
  {
    "name": "evaluate_recovery_plan",
    "description": "Structurally evaluate a recovery proposal against a seeded scenario. The proposal is a recovery option id (see find_alternative_dependencies) plus the plan version it was built against. Checks that the entity references resolve, the dependency edge exists, and the expected plan version matches the committed version, then reports remaining prerequisites and downstream effects using the residual graph. The verdict is structural only — business readiness is explicitly not assessed. Read-only: the committed plan is never modified. Unknown scenario ids and malformed proposals return a structured error; well-formed proposals that fail a check return a 'rejected' report.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "option_id": {
          "title": "Option Id",
          "type": "string"
        },
        "expected_plan_version": {
          "title": "Expected Plan Version",
          "type": "integer"
        }
      },
      "required": [
        "scenario_id",
        "option_id",
        "expected_plan_version"
      ],
      "type": "object",
      "title": "evaluate_recovery_planArguments"
    }
  },
  {
    "name": "rebalance_workload",
    "description": "Propose hypothetical workload reassignments for a staffed scenario. Given a scenario with a resources list and per-entity assignee/effort values, report each resource's capacity, unfinished assigned effort, free capacity, and overload status, then suggest deterministic moves that relieve overloaded resources, with stable suggestion ids. Effort is in abstract 'work_units' with no time dimension. Read-only: committed assignments, dependencies, statuses, and version are never changed; no delivery dates are predicted. Requires expected_plan_version (stale versions are rejected). Optionally scope the analysis to one resource with resource_id. Scenarios without staffing data return an explicit 'insufficient_data' outcome. Unknown scenario/resource ids return a structured error.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "expected_plan_version": {
          "title": "Expected Plan Version",
          "type": "integer"
        },
        "resource_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Resource Id"
        }
      },
      "required": [
        "scenario_id",
        "expected_plan_version"
      ],
      "type": "object",
      "title": "rebalance_workloadArguments"
    }
  },
  {
    "name": "analyze_blocked_tasks",
    "description": "Blocker triage (BuildBack): after a disruption, assign exactly one state to every entity — blocked, external, at_risk, healthy, or done — with a reason and an evidence path, and list the parallel work that can still proceed. Hard requires_* paths block; benefits_from edges only add risk; time delays are absorbed by entity slack so downstream work is not assumed to fail. Also reports the critical path and how the disruption changes it. Read-only: the committed plan is never modified. Unknown scenario/entity ids and invalid events return a structured error.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id",
          "type": "string"
        },
        "event_type": {
          "default": "service_outage",
          "title": "Event Type",
          "type": "string"
        },
        "reason": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Reason"
        },
        "delay_minutes": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Delay Minutes"
        }
      },
      "required": [
        "scenario_id",
        "entity_id"
      ],
      "type": "object",
      "title": "analyze_blocked_tasksArguments"
    }
  },
  {
    "name": "recalculate_delivery_forecast",
    "description": "Compare a scenario's committed critical path with the critical path after applying a proposed change set (or a drop-dependency option id), and report the delay_delta in relative duration units (steps or duration_units) — never calendar dates. The change set uses the apply_changes shape: remove_dependency/add_dependency entries. Substitute option ids (substitute:...) are rejected with guidance to pass the option's explicit changes, because only the legacy drop-dependency id format is parseable here. Invalid inputs (unknown scenario/entity, malformed changes or option id, both changes and option_id) return a structured error; a well-formed change set that produces an invalid graph (e.g. a cycle) is reported in-band with feasible: false and null forecast fields, never as a valid forecast. Read-only: the committed plan is never modified.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "changes": {
          "anyOf": [
            {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Changes"
        },
        "option_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Option Id"
        },
        "disrupted_entity_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disrupted Entity Id"
        }
      },
      "required": [
        "scenario_id"
      ],
      "type": "object",
      "title": "recalculate_delivery_forecastArguments"
    }
  },
  {
    "name": "validate_hard_constraints",
    "description": "Validate a scenario's hard constraints and dependency-graph rules for a proposed change set (default: the committed plan itself, baseline validation). The change set uses the apply_changes shape. Returns feasible plus machine-readable violations: graph faults first (ENTITY_NOT_FOUND, INVALID_DEPENDENCY, INVALID_DEPENDENCY_TYPE, CYCLIC_DEPENDENCY) then committed constraints in fixture order (currently all_entities_done_before, evaluated as a current-state invariant over committed statuses; unsupported types are violations, never silently skipped). 'feasible: true' means every graph rule passed and every constraint is supported and currently satisfied. Invalid requests (unknown scenario, malformed scenario, non-list changes, unsupported change action) return a structured error. Read-only: statuses and the committed plan are never modified, and no scoring or scheduling happens here.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "changes": {
          "anyOf": [
            {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Changes"
        }
      },
      "required": [
        "scenario_id"
      ],
      "type": "object",
      "title": "validate_hard_constraintsArguments"
    }
  },
  {
    "name": "compare_recovery_options",
    "description": "Feasibility-gate, score, rank, and attach delivery forecasts to a list of recovery option objects (each needs a unique option_id; either an explicit changes list — including substitute: options — or a legacy drop-dependency id derived into a remove_dependency change). Feasibility is gated with hard-constraint/graph validation first: infeasible options are excluded with their violations and are never scored or recommended. Score = w_delay*delay + w_cost*cost + w_risk*risk + w_strain*resource_strain + w_count*disruption_count, lower is better, ties break by ascending option_id. Default weights: delay 1.0 and 0.0 for the rest — the repository supplies no cost/risk data, those metrics are never fabricated, and a nonzero weight without a value yields an unscorable option, never a zero-filled score. delay comes from the relative critical-path forecast, resource_strain from workload overload when staffing data exists. recommendation is the top feasible, scorable option, or null with outcome no_feasible_recovery. Every entry carries its own forecast report. Weights, metric keys, and option lists are validated; malformed input returns a structured error. Read-only: nothing is committed.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "options": {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "title": "Options",
          "type": "array"
        },
        "weights": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Weights"
        }
      },
      "required": [
        "scenario_id",
        "options"
      ],
      "type": "object",
      "title": "compare_recovery_optionsArguments"
    }
  },
  {
    "name": "find_substitute_options",
    "description": "Propose substitute-service recovery options for a disrupted prerequisite. A candidate is proposed only when the entity explicitly declares 'metadata.substitutes' — a list of entity ids in the same scenario; absent or empty declarations return outcome 'no_substitute_candidates' with missing_data explaining what is missing (no shipped fixture declares any). Nothing is inferred from names, statuses, resources, or graph structure, and no service, availability, compatibility, cost, or risk is invented. Each declared candidate becomes one substitute:{prerequisite}->{substitute} option whose change set reroutes every dependency edge on the disrupted entity (one remove_dependency plus one add_dependency per dependent, preserving dependency_type and constraint_parameters), validated against hard constraints and graph rules; failures come back as feasible: false with reasons and stable violation codes. Outcome is one of substitutes_proposed, no_feasible_substitute, or no_substitute_candidates. Options carry explicit changes, so they flow into compare_recovery_options and recalculate_delivery_forecast unchanged (the legacy drop-dependency id parser does not accept substitute ids). Unknown scenario/entity ids and malformed substitutes declarations return a structured error. Read-only: nothing is committed.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id",
          "type": "string"
        }
      },
      "required": [
        "scenario_id",
        "entity_id"
      ],
      "type": "object",
      "title": "find_substitute_optionsArguments"
    }
  },
  {
    "name": "generate_recovery_schedule",
    "description": "Generate a hypothetical, read-only schedule for a scenario, optionally after a proposed recovery change set (the apply_changes shape) or a legacy drop-dependency option id, and optionally with a disrupted entity whose readiness is recomputed with blocker triage against the modified graph. Reports per-task readiness groups (completed, ready_now, waiting, blocked), parallel execution layers, a deterministic execution order, hard/soft prerequisites, critical-path membership, and — only when every task declares a duration — earliest start/finish times, waiting vs execution time, and a projected completion duration in relative units. Missing durations are never invented: the report comes back as outcome insufficient_duration_data with ordering only and missing_data explaining what is absent. With a change set, the report also includes the committed plan's baseline schedule and a baseline-vs-recovery comparison (execution orders, critical paths, completion delta only when comparable, per-task offset changes, newly blocked/unblocked tasks, parallelism changes). Proposals that create a cycle, a dangling reference, or a hard-constraint violation are reported in-band as outcome infeasible with stable violation codes and no schedule — an infeasible proposal is never ordered or recommended. scheduling_config may declare duration_unit (a label; 'minutes' additionally honors required_buffer_minutes arrival buffers) and durations (explicit caller-supplied per-entity durations). Relative time only: no calendar, start date, or working hours are modeled, and no resource constraints are applied. Unknown scenario/entity ids and malformed inputs return a structured error. Read-only: the committed plan is never modified.",
    "input_schema": {
      "properties": {
        "scenario_id": {
          "title": "Scenario Id",
          "type": "string"
        },
        "changes": {
          "anyOf": [
            {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Changes"
        },
        "option_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Option Id"
        },
        "disrupted_entity_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disrupted Entity Id"
        },
        "scheduling_config": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Scheduling Config"
        }
      },
      "required": [
        "scenario_id"
      ],
      "type": "object",
      "title": "generate_recovery_scheduleArguments"
    }
  }
] as unknown as MCPToolInfo[];
