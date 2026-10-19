// ─────────────────────────────────────────────────────────────────────────────
// Mock mode — pages are filled with captured fixture data unconditionally.
//
// While MOCK_MODE is true the API layer (mcpClient / assistantClient) never
// reaches the network: tool calls, health checks, tool discovery, and chat
// replies are served from fixtures captured from real backend runs, and the
// store seeds every result slice on startup. Set MOCK_MODE to false to return
// the dashboard to live backend data.
// ─────────────────────────────────────────────────────────────────────────────

import type {
  ChatResponse,
  ChatStep,
  MCPConnectionStatus,
  MCPToolCall,
} from '../types';
import type { ActivityEvent } from '../store/appStore';
import {
  MOCK_PLAN,
  MOCK_IMPACT,
  MOCK_TRIAGE,
  MOCK_RECOVERY,
  MOCK_COMPARISON,
  MOCK_EVALUATION,
  MOCK_SCHEDULE,
  MOCK_TOOLS,
} from './mockFixtures';

/**
 * Master switch: true = every page renders captured mock data.
 *
 * Off by default so the dashboard always reflects the live backend. Set
 * VITE_MOCK_MODE=true (see frontend/.env.example) to run fully offline for
 * screenshots/demos.
 */
export const MOCK_MODE = import.meta.env.VITE_MOCK_MODE === 'true';

// ── Tool result registry ──────────────────────────────────────────────────────

const MOCK_TOOL_RESULTS: Record<string, unknown> = {
  get_plan_state: MOCK_PLAN,
  simulate_disruption: MOCK_IMPACT,
  analyze_blocked_tasks: MOCK_TRIAGE,
  find_alternative_dependencies: MOCK_RECOVERY,
  compare_recovery_options: MOCK_COMPARISON,
  evaluate_recovery_plan: MOCK_EVALUATION,
  generate_recovery_schedule: MOCK_SCHEDULE,
};

/** Fixture for a tool, or null when no fixture was captured for it. */
export function mockToolResult(tool: string): unknown | null {
  return MOCK_TOOL_RESULTS[tool] ?? null;
}

// ── Connection / discovery ────────────────────────────────────────────────────

export function mockConnectionStatus(): MCPConnectionStatus {
  return {
    connected: true,
    serverUrl: '/api/mcp',
    serverName: 'RippleBack',
    serverVersion: '0.1.0',
    tools: MOCK_TOOLS.map((t) => t.name),
    lastCheckedAt: Date.now(),
    assistantProvider: 'deterministic',
  };
}

export function mockToolList() {
  return MOCK_TOOLS;
}

// ── Seeded MCP call log (for the Inspector) ───────────────────────────────────

const SEEDED_CALLS: Array<{ tool: string; args: Record<string, unknown>; ageMs: number }> = [
  { tool: 'get_plan_state', args: { scenario_id: 'project-full' }, ageMs: 48_000 },
  { tool: 'simulate_disruption', args: { scenario_id: 'project-full', entity_id: 'payment-api', severity: 'high' }, ageMs: 41_000 },
  { tool: 'analyze_blocked_tasks', args: { scenario_id: 'project-full', entity_id: 'payment-api', event_type: 'service_outage' }, ageMs: 40_000 },
  { tool: 'find_alternative_dependencies', args: { scenario_id: 'project-full', entity_id: 'payment-api' }, ageMs: 33_000 },
  { tool: 'compare_recovery_options', args: { scenario_id: 'project-full' }, ageMs: 27_000 },
  { tool: 'generate_recovery_schedule', args: { scenario_id: 'project-full' }, ageMs: 19_000 },
  { tool: 'evaluate_recovery_plan', args: { scenario_id: 'project-full', expected_plan_version: 1 }, ageMs: 11_000 },
];

export function mockCallLogEntries(): MCPToolCall[] {
  const now = Date.now();
  return SEEDED_CALLS.map((c, i) => {
    const startedAt = now - c.ageMs;
    const durationMs = 60 + i * 17;
    const completedAt = startedAt + durationMs;
    return {
      id: `mock-${c.tool}-${i}`,
      tool: c.tool,
      args: c.args,
      startedAt,
      completedAt,
      durationMs,
      result: mockToolResult(c.tool) ?? undefined,
      isError: false,
    };
  });
}

// ── Seeded activity log ───────────────────────────────────────────────────────

export function mockActivityLog(): ActivityEvent[] {
  const now = Date.now();
  const base: Array<[string, string, ActivityEvent['status'], string, number]> = [
    ['disruption_detected', 'Disruption detected', 'done', 'Payment API service — service outage (high)', 56_000],
    ['simulation_completed', 'Simulation completed', 'done', '2 tasks at risk, 5 blocked', 49_000],
    ['impact_analyzed', 'Impact analyzed', 'done', '1 directly affected, 4 transitive', 47_000],
    ['recovery_generated', 'Recovery options generated', 'done', '1 option ranked, 0 excluded', 39_000],
    ['option_selected', 'Option selected for review', 'done', 'drop-dependency:backend-integration->payment-api', 30_000],
    ['schedule_generated', 'Proposed schedule generated', 'done', 'Feasible · 9 duration_units', 22_000],
    ['assistant_turn', 'Assistant turn completed', 'done', 'question · 1 MCP call(s)', 8_000],
  ];
  return base.map(([stage, label, status, detail, ageMs]) => ({
    id: `mock-activity-${stage}`,
    stage,
    label,
    status,
    detail,
    ts: now - ageMs,
  }));
}

// ── Seeded assistant conversation ─────────────────────────────────────────────

function step(tool: string, args: Record<string, unknown>, summary: string, durationMs: number): ChatStep {
  return {
    tool,
    arguments: args,
    status: 'ok',
    duration_ms: durationMs,
    summary,
    result: mockToolResult(tool) ?? undefined,
  };
}

export function mockChatSeed(): { id: string; role: 'user' | 'assistant'; text: string; ts: number; intent?: string; provider?: string; providerNotes?: string[]; steps?: ChatStep[]; needsClarification?: boolean; truncated?: boolean }[] {
  const now = Date.now();
  return [
    {
      id: 'mock-chat-user-1',
      role: 'user',
      text: 'What is blocked after the Payment API outage?',
      ts: now - 62_000,
    },
    {
      id: 'mock-chat-assistant-1',
      role: 'assistant',
      text:
        '5 tasks are blocked behind payment-api: backend-integration, integration-testing, release-validation, production-release and unit-testing (soft dependency). ' +
        '2 tasks remain at risk, and frontend-integration plus release-notes can still proceed in parallel.',
      ts: now - 61_000,
      intent: 'question',
      provider: 'deterministic',
      providerNotes: ['Served from mock fixtures (MOCK_MODE=true).'],
      needsClarification: false,
      truncated: false,
      steps: [
        step('get_plan_state', { scenario_id: 'project-full' }, 'Loaded 10 entities, 6 dependencies', 74),
        step(
          'analyze_blocked_tasks',
          { scenario_id: 'project-full', entity_id: 'payment-api', event_type: 'service_outage' },
          '5 blocked · 2 at risk · 2 healthy',
          91,
        ),
      ],
    },
  ];
}

// ── Mock assistant reply ──────────────────────────────────────────────────────

export function mockChatResponse(message: string): ChatResponse {
  const steps: ChatStep[] = [
    step('get_plan_state', { scenario_id: 'project-full' }, 'Loaded 10 entities, 6 dependencies', 68),
    step(
      'analyze_blocked_tasks',
      { scenario_id: 'project-full', entity_id: 'payment-api', event_type: 'service_outage' },
      '5 blocked · 2 at risk · 2 healthy',
      83,
    ),
  ];
  return {
    reply:
      `Mock reply (MOCK_MODE=true) for: "${message}"\n\n` +
      'The Payment API outage blocks 5 tasks (backend-integration, integration-testing, release-validation, production-release, unit-testing). ' +
      'The ranked recovery option — drop-dependency:backend-integration->payment-api — restores a feasible schedule of 9 duration_units.',
    intent: 'question',
    provider: 'deterministic',
    provider_notes: ['Served from mock fixtures (MOCK_MODE=true); no backend call was made.'],
    steps,
    needs_clarification: false,
    scenario_id: 'project-full',
    tool_calls_made: steps.length,
    truncated: false,
    duration_ms: 151,
  };
}
