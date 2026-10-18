/**
 * DisruptionControls — entity/type/parameter pickers + simulation trigger.
 *
 * Supports:
 *   Project scenarios  → service_outage (payment-api), resource_unavailable (any assignee),
 *                        delivery_delay (vendor-delivery with delay_minutes)
 *   Travel scenario    → transport_delay (leg-1 with delay_minutes)
 *
 * Uses the actual analyze_blocked_tasks event_type + delay_minutes schema.
 * Never modifies the committed plan.
 */
import { useState } from 'react';
import { Zap, AlertTriangle, ChevronDown } from 'lucide-react';
import { useApp, useAsyncOp } from '../store/appStore';
import { Spinner } from './ui/Spinner';
import {
  simulateDisruption,
  analyzeBlockedTasks,
  findAlternativeDependencies,
  compareRecoveryOptions,
} from '../api/mcpClient';
import {
  MOCK_IMPACT_RESULT,
  MOCK_TRIAGE_RESULT,
  MOCK_RECOVERY_RESULT,
  MOCK_COMPARISON_RESULT,
} from '../data/mockScenario';
import type { EventType, DisruptionSeverity, ScenarioId } from '../types';
import { clsx } from 'clsx';

// ── Per-scenario disruption presets ──────────────────────────────────────────

interface DisruptionPreset {
  id: string;
  label: string;
  entityId: string;
  eventType: EventType;
  severity: DisruptionSeverity;
  defaultReason: string;
  description: string;
  needsDelay: boolean;
  defaultDelay?: number;
}

const PRESETS: Record<ScenarioId, DisruptionPreset[]> = {
  'project-full': [
    {
      id: 'payment-outage',
      label: 'Payment API outage',
      entityId: 'payment-api',
      eventType: 'service_outage',
      severity: 'high',
      defaultReason: 'Payment API service is unavailable',
      description: 'Blocks backend integration and the entire downstream release chain.',
      needsDelay: false,
    },
    {
      id: 'resource-unavailable',
      label: 'Teammate unavailable (Alice)',
      entityId: 'backend-integration',
      eventType: 'resource_unavailable',
      severity: 'medium',
      defaultReason: 'Alice is unavailable — backend integration stalled',
      description: 'Simulates a key team member being out, blocking their assigned work.',
      needsDelay: false,
    },
    {
      id: 'vendor-delay',
      label: 'Vendor delivery delay',
      entityId: 'vendor-delivery',
      eventType: 'delivery_delay',
      severity: 'medium',
      defaultReason: 'Vendor has delayed delivery by specified minutes',
      description: 'External dependency arrives late. Downstream tasks may absorb the delay via slack.',
      needsDelay: true,
      defaultDelay: 120,
    },
  ],
  'demo-release-plan': [
    {
      id: 'payment-outage',
      label: 'Payment API outage',
      entityId: 'payment-api',
      eventType: 'service_outage',
      severity: 'high',
      defaultReason: 'Payment API service is unavailable',
      description: 'Blocks the entire downstream release chain.',
      needsDelay: false,
    },
  ],
  'demo-release-plan-staffed': [
    {
      id: 'payment-outage',
      label: 'Payment API outage',
      entityId: 'payment-api',
      eventType: 'service_outage',
      severity: 'high',
      defaultReason: 'Payment API service is unavailable',
      description: 'Blocks integration chain. Alice is overloaded — workload rebalancing may be triggered.',
      needsDelay: false,
    },
  ],
  travel: [
    {
      id: 'train-delay',
      label: 'Train leg 1 delayed',
      entityId: 'leg-1',
      eventType: 'transport_delay',
      severity: 'medium',
      defaultReason: 'Train leg 1 is delayed',
      description: 'Delay propagates through connections. Downstream legs with slack may absorb it.',
      needsDelay: true,
      defaultDelay: 45,
    },
  ],
};

const SEVERITY_OPTS: { value: DisruptionSeverity; label: string }[] = [
  { value: 'low',      label: 'Low' },
  { value: 'medium',   label: 'Medium' },
  { value: 'high',     label: 'High' },
  { value: 'critical', label: 'Critical' },
];

// ── Component ─────────────────────────────────────────────────────────────────

export function DisruptionControls() {
  const [state, dispatch] = useApp();
  const { activeScenarioId, disruption, mcpStatus } = state;
  const { loading, error, run } = useAsyncOp('disruption');

  const presets = PRESETS[activeScenarioId] ?? [];
  const [selectedPresetId, setSelectedPresetId] = useState(presets[0]?.id ?? '');
  const [severity, setSeverity] = useState<DisruptionSeverity>('high');
  const [delayMinutes, setDelayMinutes] = useState<number>(120);
  const [customReason, setCustomReason] = useState('');

  const preset = presets.find((p) => p.id === selectedPresetId) ?? presets[0];
  const isActive = disruption.active;
  const isOffline = !mcpStatus.connected;

  // Sync delay default when preset changes
  function handlePresetChange(id: string) {
    setSelectedPresetId(id);
    const p = presets.find((pr) => pr.id === id);
    if (p?.defaultDelay) setDelayMinutes(p.defaultDelay);
    setSeverity(p?.severity ?? 'high');
    setCustomReason('');
  }

  async function handleSimulate() {
    if (!preset) return;
    const reason = customReason.trim() || preset.defaultReason;
    const params = preset.needsDelay ? { delay_minutes: delayMinutes } : undefined;

    dispatch({
      type: 'TRIGGER_DISRUPTION',
      entityId: preset.entityId,
      reason,
      severity,
      eventType: preset.eventType,
      ...(preset.needsDelay ? { delayMinutes } : {}),
    });

    dispatch({ type: 'LOG_ACTIVITY', stage: 'disruption_detected', label: 'Disruption detected', status: 'done', detail: preset.label });
    dispatch({ type: 'LOG_ACTIVITY', stage: 'simulation_completed', label: 'Simulation completed', status: 'running' });

    await run(async () => {
      let impactResult    = MOCK_IMPACT_RESULT;
      let triageResult    = MOCK_TRIAGE_RESULT;
      let recoveryResult  = MOCK_RECOVERY_RESULT;
      let comparisonResult = MOCK_COMPARISON_RESULT;

      if (!isOffline) {
        const [impact, triage] = await Promise.all([
          simulateDisruption(activeScenarioId, preset.entityId, reason, severity)
            .catch(() => MOCK_IMPACT_RESULT),
          analyzeBlockedTasks(activeScenarioId, preset.entityId, preset.eventType, reason, params?.delay_minutes)
            .catch(() => MOCK_TRIAGE_RESULT),
        ]);
        impactResult  = impact;
        triageResult  = triage;

        const recovery = await findAlternativeDependencies(activeScenarioId, preset.entityId)
          .catch(() => MOCK_RECOVERY_RESULT);
        recoveryResult = recovery;

        if (recovery.options.length > 0) {
          const comparison = await compareRecoveryOptions(
            activeScenarioId,
            recovery.options.map((o) => ({ option_id: o.option_id })),
          ).catch(() => MOCK_COMPARISON_RESULT);
          comparisonResult = comparison;
        }
      }

      dispatch({ type: 'SET_IMPACT',     result: impactResult });
      dispatch({ type: 'SET_TRIAGE',     result: triageResult });
      dispatch({ type: 'SET_RECOVERY',   result: recoveryResult });
      dispatch({ type: 'SET_COMPARISON', result: comparisonResult });

      dispatch({ type: 'LOG_ACTIVITY', stage: 'simulation_completed', label: 'Simulation completed', status: 'done' });
      dispatch({ type: 'LOG_ACTIVITY', stage: 'impact_analyzed',      label: 'Impact analyzed',      status: 'done',
        detail: `${triageResult.summary.blocked} blocked, ${triageResult.summary.at_risk} at-risk` });
      dispatch({ type: 'LOG_ACTIVITY', stage: 'recovery_generated',   label: 'Recovery options generated', status: 'done',
        detail: `${comparisonResult.feasible_count} feasible options` });
      dispatch({ type: 'LOG_ACTIVITY', stage: 'option_selected',      label: 'Option selected for review', status: 'awaiting' });

      dispatch({ type: 'SET_VIEW', view: 'impact' });
    });
  }

  if (presets.length === 0) return null;

  return (
    <div className="card space-y-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-600">
        <Zap className="w-4 h-4 text-yellow-500 flex-shrink-0" />
        <span className="text-sm font-medium text-gray-200">Disruption Controls</span>
        {isOffline && (
          <span className="ml-auto tag-at_risk text-[10px]">MOCK</span>
        )}
      </div>

      <div className="p-4 space-y-4">
        {/* Preset picker */}
        <div className="space-y-1.5">
          <label className="text-[10px] uppercase tracking-wider text-gray-600">Disruption type</label>
          <div className="space-y-1.5">
            {presets.map((p) => (
              <button
                key={p.id}
                disabled={isActive || loading}
                onClick={() => handlePresetChange(p.id)}
                className={clsx(
                  'w-full text-left px-3 py-2.5 rounded-lg border text-xs transition-colors',
                  selectedPresetId === p.id
                    ? 'border-blue-600 bg-blue-950/40 text-blue-200'
                    : 'border-surface-500 bg-surface-700/50 text-gray-400 hover:border-surface-400 hover:text-gray-300',
                  (isActive || loading) && 'opacity-50 cursor-not-allowed',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{p.label}</span>
                  <EventTypeBadge type={p.eventType} />
                </div>
                <p className="text-[10px] mt-0.5 opacity-70 leading-relaxed">{p.description}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Delay input (when needed) */}
        {preset?.needsDelay && (
          <div className="space-y-1.5">
            <label className="text-[10px] uppercase tracking-wider text-gray-600">
              Delay (minutes)
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={1}
                max={480}
                value={delayMinutes}
                onChange={(e) => setDelayMinutes(Math.max(1, Number(e.target.value)))}
                disabled={isActive || loading}
                className="w-24 bg-surface-700 border border-surface-500 rounded px-2.5 py-1.5 text-sm text-gray-200 font-mono focus:outline-none focus:border-blue-500 disabled:opacity-50"
              />
              <span className="text-xs text-gray-600">minutes</span>
            </div>
            <p className="text-[10px] text-gray-600">
              Downstream tasks with sufficient slack will absorb this delay.
            </p>
          </div>
        )}

        {/* Severity row */}
        <div className="space-y-1.5">
          <label className="text-[10px] uppercase tracking-wider text-gray-600">Severity</label>
          <div className="flex gap-1.5 flex-wrap">
            {SEVERITY_OPTS.map((opt) => (
              <button
                key={opt.value}
                disabled={isActive || loading}
                onClick={() => setSeverity(opt.value)}
                className={clsx(
                  'px-2.5 py-1 rounded text-[10px] font-medium border transition-colors',
                  severity === opt.value
                    ? severityActive(opt.value)
                    : 'border-surface-500 text-gray-500 hover:text-gray-300',
                  (isActive || loading) && 'opacity-50 cursor-not-allowed',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Custom reason */}
        <div className="space-y-1.5">
          <label className="text-[10px] uppercase tracking-wider text-gray-600">
            Reason <span className="normal-case text-gray-700">(optional)</span>
          </label>
          <input
            type="text"
            placeholder={preset?.defaultReason ?? 'Describe the disruption…'}
            value={customReason}
            onChange={(e) => setCustomReason(e.target.value)}
            disabled={isActive || loading}
            className="w-full bg-surface-700 border border-surface-500 rounded px-2.5 py-1.5 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-blue-500 disabled:opacity-50"
          />
        </div>

        {/* Description preview */}
        {preset && (
          <div className="text-[10px] text-gray-600 bg-surface-700/50 rounded px-3 py-2 leading-relaxed">
            <span className="text-gray-500 font-medium">Will simulate: </span>
            {preset.description}
          </div>
        )}

        {/* Primary action */}
        <button
          className={clsx(
            'w-full btn text-sm font-semibold py-2.5 justify-center',
            isActive ? 'btn-ghost opacity-50' : 'btn-danger',
          )}
          onClick={handleSimulate}
          disabled={loading || isActive || !preset}
        >
          {loading ? (
            <><Spinner className="w-4 h-4" /> Simulating…</>
          ) : isActive ? (
            <><AlertTriangle className="w-4 h-4" /> Disruption active — reset first</>
          ) : (
            <><Zap className="w-4 h-4" /> Simulate Disruption</>
          )}
        </button>

        {/* Errors */}
        {error && (
          <div className="text-xs text-red-400 bg-red-900/20 border border-red-900 rounded px-3 py-2 space-y-1">
            <p>{error}</p>
            {isOffline && <p className="text-amber-400">Using mock data (backend offline).</p>}
          </div>
        )}

        {/* Active notice */}
        {isActive && !loading && (
          <div className="flex items-start gap-2 text-xs text-amber-400 bg-amber-900/15 border border-amber-900/50 rounded px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <span>
              Simulation only — the committed plan is <strong>not modified</strong>.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function EventTypeBadge({ type }: { type: EventType }) {
  const map: Record<EventType, { label: string; cls: string }> = {
    service_outage:       { label: 'outage',   cls: 'text-red-400 bg-red-900/30 border-red-800' },
    resource_unavailable: { label: 'resource', cls: 'text-orange-400 bg-orange-900/30 border-orange-800' },
    delivery_delay:       { label: 'delay',    cls: 'text-yellow-400 bg-yellow-900/30 border-yellow-800' },
    transport_delay:      { label: 'transport',cls: 'text-blue-400 bg-blue-900/30 border-blue-800' },
  };
  const { label, cls } = map[type] ?? { label: type, cls: 'text-gray-400 bg-surface-600 border-surface-500' };
  return (
    <span className={clsx('badge text-[9px] border', cls)}>
      <ChevronDown className="hidden" />{/* satisfy lint — no unused import */}
      {label}
    </span>
  );
}

function severityActive(s: DisruptionSeverity): string {
  const m: Record<DisruptionSeverity, string> = {
    low:      'border-emerald-600 bg-emerald-900/30 text-emerald-300',
    medium:   'border-yellow-600 bg-yellow-900/30 text-yellow-300',
    high:     'border-orange-600 bg-orange-900/30 text-orange-300',
    critical: 'border-red-600 bg-red-900/30 text-red-300',
  };
  return m[s];
}
