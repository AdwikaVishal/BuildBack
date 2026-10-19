// ─────────────────────────────────────────────────────────────────────────────
// Static display metadata for the seeded backend scenario fixtures.
//
// Only human-readable labels/descriptions live here — every value rendered
// as data (entities, dependencies, statuses, analysis results) comes from
// the real MCP server. Scenario ids match src/rippleback/scenarios.py.
// ─────────────────────────────────────────────────────────────────────────────

import type { ScenarioId } from '../types';

export interface ScenarioInfo {
  label: string;
  description: string;
}

export const SCENARIO_INFO: Record<ScenarioId, ScenarioInfo> = {
  'project-full': {
    label: 'Full release project',
    description: '10-entity release plan with vendor, slack, and soft dependencies',
  },
  'demo-release-plan': {
    label: 'Payment API release',
    description: '6-entity payment API release chain',
  },
  'demo-release-plan-staffed': {
    label: 'Staffed release plan',
    description: 'Payment API release with synthetic staffing (alice, bob)',
  },
  travel: {
    label: 'Travel connection',
    description: 'Train connection with delay buffers',
  },
};

export const SCENARIO_IDS: ScenarioId[] = [
  'project-full',
  'demo-release-plan',
  'demo-release-plan-staffed',
  'travel',
];
