// ─────────────────────────────────────────────────────────────────────────────
// Scenario display helpers.
//
// Scenario ids and their metadata come from the live backend (GET
// /api/status → `scenarios.items`), which lists both seeded fixtures and
// imported PM plans. Nothing is hardcoded here: the dashboard renders whatever
// the backend reports. These helpers only turn an id or summary into a
// human-readable label/description, and are safe fallbacks when no metadata
// is available yet.
// ─────────────────────────────────────────────────────────────────────────────

import type { ScenarioSummary } from '../types';

/** Turn a scenario id like `linear:eng-team` into `Linear · eng-team`. */
export function prettyScenarioLabel(scenarioId: string): string {
  if (!scenarioId) return 'No scenario';
  const [provider, ...rest] = scenarioId.split(':');
  const tail = rest.join(':');
  if (!tail) return scenarioId;
  const providerLabel = provider.charAt(0).toUpperCase() + provider.slice(1);
  return `${providerLabel} · ${tail}`;
}

/** Best display label for a scenario: its plan id, else a prettified id. */
export function scenarioLabel(scenarioId: string, summary?: ScenarioSummary): string {
  return summary?.plan_id || prettyScenarioLabel(scenarioId);
}

/** Honest one-line description built only from real metadata. */
export function scenarioDescription(summary?: ScenarioSummary): string {
  if (!summary) return '';
  const parts: string[] = [];
  if (summary.source) parts.push(`source: ${summary.source}`);
  if (summary.entity_count != null) parts.push(`${summary.entity_count} entities`);
  if (summary.version != null) parts.push(`v${summary.version}`);
  if (summary.status) parts.push(summary.status);
  if (summary.open_disruptions) parts.push(`${summary.open_disruptions} open disruption(s)`);
  return parts.join(' · ');
}
