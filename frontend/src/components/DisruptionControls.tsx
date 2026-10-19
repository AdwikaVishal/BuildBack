/**
 * DisruptionControls — scenario presets, parameters, and the simulation trigger.
 *
 * Supports the per-scenario presets defined against the real backend event
 * types (service_outage, resource_unavailable, delivery_delay,
 * transport_delay) and submits them through the actual MCP tool schemas.
 *
 * Honesty rules:
 *  - No mock fallbacks: a failed request surfaces as an error, never as
 *    fabricated data.
 *  - Simulation is read-only: the committed plan is never modified.
 *  - Stale responses (e.g. after a scenario switch) are discarded instead of
 *    overwriting newer state.
 */
import { useMemo, useRef, useState } from 'react';
import { Zap, AlertTriangle, Lock } from 'lucide-react';
import { useApp, useAsyncOp } from '../store/appStore';
import { Spinner } from './ui/Spinner';
import {
  simulateDisruption,
  analyzeBlockedTasks,
  findAlternativeDependencies,
  compareRecoveryOptions,
} from '../api/mcpClient';
import type { EventType, DisruptionSeverity, Scenario } from '../types';
import { clsx } from 'clsx';

// ── Disruption presets (derived from the live plan) ──────────────────────────

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

/**
 * Build presets from the loaded plan so the simulator works for any scenario,
 * including imported PM plans — never a hardcoded scenario→preset table.
 * Every unfinished entity becomes a candidate; external entities are modelled
 * as delivery delays (which can carry a delay parameter), everything else as
 * a service outage.
 */
function buildPresets(plan: Scenario | null): DisruptionPreset[] {
  if (!plan) return [];
  return plan.entities
    .filter((entity) => entity.status !== 'done')
    .map((entity) => {
      const external = Boolean(entity.metadata?.external);
      const name = entity.name || entity.id;
      return {
        id: `entity:${entity.id}`,
        label: name,
        entityId: entity.id,
        eventType: external ? 'delivery_delay' : 'service_outage',
        severity: 'high' as DisruptionSeverity,
        defaultReason: external ? `${name} delivery is delayed` : `${name} is unavailable`,
        description: 'Simulate a disruption here and trace its blast radius through the plan.',
        needsDelay: external,
        defaultDelay: external ? 120 : undefined,
      };
    });
}

const SEVERITY_OPTS: { value: DisruptionSeverity; label: string }[] = [
  { value: 'low',      label: 'Low' },
  { value: 'medium',   label: 'Medium' },
  { value: 'high',     label: 'High' },
  { value: 'critical', label: 'Critical' },
];

// ── Component ─────────────────────────────────────────────────────────────────

export function DisruptionControls() {
  const [state, dispatch] = useApp();
  const { activeScenarioId, planData, disruption, mcpStatus } = state;
  const { loading, error, run } = useAsyncOp('disruption');

  const presets = useMemo(() => buildPresets(planData), [planData]);
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [severity, setSeverity] = useState<DisruptionSeverity>('high');
  const [delayMinutes, setDelayMinutes] = useState<number>(120);
  const [customReason, setCustomReason] = useState('');

  // Keep the selection valid across scenario switches / plan (re)loads. Reset
  // during render (React's recommended pattern) rather than in an effect.
  const [prevPresets, setPrevPresets] = useState(presets);
  if (prevPresets !== presets) {
    setPrevPresets(presets);
    const first = presets[0];
    setSelectedPresetId(first?.id ?? '');
    if (first) {
      setSeverity(first.severity);
      if (first.defaultDelay) setDelayMinutes(first.defaultDelay);
    }
  }

  const preset = presets.find((p) => p.id === selectedPresetId) ?? presets[0];
  const isActive = disruption.active;
  const isOffline = !mcpStatus.connected;

  // Monotonic token: responses belonging to an outdated request (e.g. the
  // user switched scenario mid-flight) are discarded instead of dispatched.
  const requestSeq = useRef(0);

  function handlePresetChange(id: string) {
    setSelectedPresetId(id);
    const p = presets.find((pr) => pr.id === id);
    if (p?.defaultDelay) setDelayMinutes(p.defaultDelay);
    setSeverity(p?.severity ?? 'high');
    setCustomReason('');
  }

  async function handleSimulate() {
    if (!preset) return;
    const token = ++requestSeq.current;
    const isCurrent = () => token === requestSeq.current;
    const reason = customReason.trim() || preset.defaultReason;
    const delay = preset.needsDelay ? delayMinutes : undefined;

    dispatch({
      type: 'TRIGGER_DISRUPTION',
      entityId: preset.entityId,
      reason,
      severity,
      eventType: preset.eventType,
      ...(delay != null ? { delayMinutes: delay } : {}),
    });

    dispatch({ type: 'LOG_ACTIVITY', stage: 'disruption_detected', label: 'Disruption detected', status: 'done', detail: preset.label });
    dispatch({ type: 'LOG_ACTIVITY', stage: 'simulation_completed', label: 'Simulation completed', status: 'running' });

    await run(async () => {
      try {
        // Real backend calls only — every failure surfaces as an error below.
        const [impact, triage] = await Promise.all([
          simulateDisruption(activeScenarioId, preset.entityId, reason, severity),
          analyzeBlockedTasks(activeScenarioId, preset.entityId, preset.eventType, reason, delay),
        ]);
        if (!isCurrent()) return null;

        dispatch({ type: 'SET_IMPACT', result: impact });
        dispatch({ type: 'SET_TRIAGE', result: triage });
        dispatch({ type: 'LOG_ACTIVITY', stage: 'simulation_completed', label: 'Simulation completed', status: 'done' });
        dispatch({
          type: 'LOG_ACTIVITY', stage: 'impact_analyzed', label: 'Impact analyzed', status: 'done',
          detail: `${triage.summary.blocked} blocked, ${triage.summary.at_risk} at-risk`,
        });

        const recovery = await findAlternativeDependencies(activeScenarioId, preset.entityId);
        if (!isCurrent()) return null;
        dispatch({ type: 'SET_RECOVERY', result: recovery });

        if (recovery.options.length > 0) {
          const comparison = await compareRecoveryOptions(
            activeScenarioId,
            recovery.options.map((o) => ({ option_id: o.option_id })),
          );
          if (!isCurrent()) return null;
          dispatch({ type: 'SET_COMPARISON', result: comparison });
          dispatch({
            type: 'LOG_ACTIVITY', stage: 'recovery_generated', label: 'Recovery options generated', status: 'done',
            detail: `${comparison.feasible_count} feasible options`,
          });
        } else {
          dispatch({
            type: 'LOG_ACTIVITY', stage: 'recovery_generated', label: 'Recovery options generated', status: 'done',
            detail: 'no alternatives found for this disruption',
          });
        }

        dispatch({ type: 'SET_VIEW', view: 'impact' });
        return null;
      } catch (err) {
        if (isCurrent()) {
          dispatch({
            type: 'LOG_ACTIVITY', stage: 'simulation_completed', label: 'Simulation completed', status: 'failed',
            detail: err instanceof Error ? err.message : String(err),
          });
        }
        throw err;
      }
    });
  }

  if (presets.length === 0) return null;

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-700">
        <Zap className="w-4 h-4 text-acid flex-shrink-0" />
        <span className="text-sm font-semibold text-ink font-display">Simulate Disruption</span>
        <span className="ml-auto badge bg-surface-800 text-ink-muted border border-surface-700 text-[11px]">
          <Lock className="w-2.5 h-2.5" /> READ-ONLY
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Preset picker */}
        <fieldset className="space-y-1.5" disabled={isActive || loading}>
          <legend className="label-tech mb-1.5">Disruption scenario</legend>
          <div className="space-y-1.5">
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => handlePresetChange(p.id)}
                aria-pressed={selectedPresetId === p.id}
                className={clsx(
                  'w-full text-left px-3 py-2.5 rounded-lg border text-xs transition-colors',
                  selectedPresetId === p.id
                    ? 'border-acid/60 bg-acid/5 text-ink'
                    : 'border-surface-700 bg-surface-900/40 text-ink-secondary hover:border-surface-600 hover:text-ink',
                  (isActive || loading) && 'opacity-50 cursor-not-allowed',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{p.label}</span>
                  <EventTypeBadge type={p.eventType} />
                </div>
                <p className="text-[11px] mt-0.5 opacity-70 leading-relaxed">{p.description}</p>
              </button>
            ))}
          </div>
        </fieldset>

        {/* Delay input (when needed) */}
        {preset?.needsDelay && (
          <div className="space-y-1.5">
            <label htmlFor="rb-delay" className="label-tech">Delay (minutes)</label>
            <div className="flex items-center gap-2">
              <input
                id="rb-delay"
                type="number"
                min={1}
                max={480}
                value={delayMinutes}
                onChange={(e) => setDelayMinutes(Math.max(1, Number(e.target.value)))}
                disabled={isActive || loading}
                className="w-24 bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-sm text-ink font-mono focus:outline-none focus:border-acid disabled:opacity-50"
              />
              <span className="text-xs text-ink-muted">minutes</span>
            </div>
            <p className="text-[11px] text-ink-muted">
              Downstream tasks with sufficient slack will absorb this delay.
            </p>
          </div>
        )}

        {/* Severity row */}
        <fieldset className="space-y-1.5" disabled={isActive || loading}>
          <legend className="label-tech mb-1.5">Severity</legend>
          <div className="flex gap-1.5 flex-wrap">
            {SEVERITY_OPTS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setSeverity(opt.value)}
                aria-pressed={severity === opt.value}
                className={clsx(
                  'px-2.5 py-1 rounded-full text-[11px] font-semibold border transition-colors',
                  severity === opt.value
                    ? severityActive(opt.value)
                    : 'border-surface-700 text-ink-muted hover:text-ink-secondary',
                  (isActive || loading) && 'opacity-50 cursor-not-allowed',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </fieldset>

        {/* Custom reason */}
        <div className="space-y-1.5">
          <label htmlFor="rb-reason" className="label-tech">
            Reason <span className="normal-case text-ink-muted/70">(optional)</span>
          </label>
          <input
            id="rb-reason"
            type="text"
            placeholder={preset?.defaultReason ?? 'Describe the disruption…'}
            value={customReason}
            onChange={(e) => setCustomReason(e.target.value)}
            disabled={isActive || loading}
            className="w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-xs text-ink placeholder-ink-muted focus:outline-none focus:border-acid disabled:opacity-50"
          />
        </div>

        {/* Description preview */}
        {preset && (
          <div className="text-[11px] text-ink-muted bg-surface-900/60 border border-surface-700 rounded-lg px-3 py-2 leading-relaxed">
            <span className="text-ink-secondary font-semibold">Will simulate: </span>
            {preset.description}
          </div>
        )}

        {/* Primary action */}
        <button
          className={clsx(
            'w-full btn text-sm py-2.5',
            isActive ? 'btn-ghost opacity-60' : 'btn-primary',
          )}
          onClick={handleSimulate}
          disabled={loading || isActive || !preset}
        >
          {loading ? (
            <><Spinner className="w-4 h-4" /> Simulating…</>
          ) : isActive ? (
            <><AlertTriangle className="w-4 h-4" /> Disruption active — reset to run another</>
          ) : (
            <><Zap className="w-4 h-4" /> Simulate Disruption</>
          )}
        </button>

        {/* Errors — surfaced honestly, never masked with sample data */}
        {error && (
          <div role="alert" className="text-xs text-fail bg-fail/5 border border-fail/40 rounded-lg px-3 py-2 space-y-1">
            <p className="font-semibold">Simulation failed</p>
            <p className="font-mono break-words">{error}</p>
            {isOffline && (
              <p className="text-ink-muted">
                The MCP backend is unreachable. Start the server and adapter (see README) — no mock data is shown.
              </p>
            )}
          </div>
        )}

        {/* Active notice */}
        {isActive && !loading && (
          <div className="flex items-start gap-2 text-xs text-warn bg-warn/5 border border-warn/40 rounded-lg px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <span>
              Hypothetical simulation only — the committed plan is <strong>not modified</strong>.
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
    service_outage:       { label: 'outage',    cls: 'text-fail bg-fail/10 border-fail/40' },
    resource_unavailable: { label: 'resource',  cls: 'text-warn bg-warn/10 border-warn/40' },
    delivery_delay:       { label: 'delay',     cls: 'text-warn bg-warn/10 border-warn/40' },
    transport_delay:      { label: 'transport', cls: 'text-info bg-info/10 border-info/40' },
  };
  const { label, cls } = map[type] ?? { label: type, cls: 'text-ink-secondary bg-surface-800 border-surface-700' };
  return <span className={clsx('badge border', cls)}>{label}</span>;
}

function severityActive(s: DisruptionSeverity): string {
  const m: Record<DisruptionSeverity, string> = {
    low:      'border-ok/60 bg-ok/10 text-ok',
    medium:   'border-warn/60 bg-warn/10 text-warn',
    high:     'border-fail/60 bg-fail/10 text-fail',
    critical: 'border-fail bg-fail/25 text-fail',
  };
  return m[s];
}
