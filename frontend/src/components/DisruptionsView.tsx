/**
 * DisruptionsView — the simulator workspace.
 * Wraps the real DisruptionControls (which call simulate_disruption,
 * analyze_blocked_tasks, find_alternative_dependencies, and
 * compare_recovery_options) plus the current simulation status.
 */
import { Zap, ArrowRight, RotateCcw } from 'lucide-react';
import { useApp } from '../store/appStore';
import { DisruptionControls } from './DisruptionControls';
import { StatusBadge } from './ui/StatusBadge';
import { EmptyState } from './ui/EmptyState';
import { clsx } from 'clsx';

const SEVERITY_TONE: Record<string, string> = {
  low:      'text-ink-secondary',
  medium:   'text-warn',
  high:     'text-warn',
  critical: 'text-fail',
};

export function DisruptionsView() {
  const [state, dispatch] = useApp();
  const { disruption, triageResult, recoveryResult, comparisonResult, mcpStatus, activeScenarioId } = state;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
      {/* Simulator */}
      <div className="xl:col-span-3 space-y-4">
        <DisruptionControls />

        {!mcpStatus.connected && (
          <div className="card p-4 border-fail/40">
            <p className="text-xs text-fail font-semibold mb-1">Backend unreachable</p>
            <p className="text-xs text-ink-muted leading-relaxed">
              Simulating requires the MCP server and adapter to be running. Nothing is simulated
              locally — the dashboard will not invent results while offline.
            </p>
          </div>
        )}
      </div>

      {/* Current state */}
      <div className="xl:col-span-2 space-y-4">
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-acid" />
              <span className="text-sm font-semibold text-ink">Current Simulation</span>
            </div>
            {disruption.active && <span className="tag-simulated">SIMULATED</span>}
          </div>
          <div className="p-4 space-y-3">
            {disruption.active ? (
              <>
                <div className="flex items-center gap-2 flex-wrap">
                  <StatusBadge status="blocked" />
                  <span className="text-sm text-ink font-medium">{disruption.entityId}</span>
                </div>
                <p className="text-xs text-ink-secondary leading-relaxed">{disruption.reason}</p>
                <div className="flex gap-3 text-[11px] text-ink-muted">
                  <span>Severity: <span className={clsx('font-semibold', SEVERITY_TONE[disruption.severity] ?? 'text-ink-secondary')}>{disruption.severity}</span></span>
                  <span>Event: <span className="font-mono text-ink-secondary">{disruption.eventType}</span></span>
                  {disruption.delayMinutes != null && (
                    <span>Delay: <span className="font-mono text-ink-secondary">{disruption.delayMinutes} min</span></span>
                  )}
                </div>
                <div className="flex flex-wrap gap-2 pt-1">
                  <button className="btn-primary text-xs" onClick={() => dispatch({ type: 'SET_VIEW', view: 'impact' })}>
                    See impact <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                  <button
                    className="btn-ghost text-xs"
                    onClick={() => dispatch({ type: 'RESET_DISRUPTION' })}
                  >
                    <RotateCcw className="w-3.5 h-3.5" /> Reset simulation
                  </button>
                </div>
              </>
            ) : (
              <EmptyState
                icon={Zap}
                title="Nothing simulated yet"
                description={`Pick a disruption preset for ${activeScenarioId} and run it to see triage, impact, and recovery options.`}
              />
            )}
          </div>
        </div>

        {/* After-math summary */}
        {disruption.active && (triageResult || recoveryResult || comparisonResult) && (
          <div className="card">
            <div className="card-header">
              <span className="text-sm font-semibold text-ink">After the simulation</span>
            </div>
            <div className="p-4 space-y-3 text-xs">
              {triageResult && (
                <div className="grid grid-cols-2 gap-2">
                  {(['blocked', 'at_risk', 'external', 'healthy', 'done'] as const).map((k) => (
                    <div key={k} className="rounded-lg bg-surface-900 border border-border px-3 py-2">
                      <div className="mb-1"><StatusBadge status={k} /></div>
                      <p className="text-xl font-bold font-mono text-ink leading-none">
                        {triageResult.summary[k] ?? 0}
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {recoveryResult && (
                <p className="text-ink-muted">
                  {recoveryResult.options.length} recovery alternative
                  {recoveryResult.options.length === 1 ? '' : 's'} found by{' '}
                  <span className="font-mono text-ink-secondary">find_alternative_dependencies</span>.
                </p>
              )}
              {comparisonResult && (
                <p className="text-ink-muted">
                  {comparisonResult.feasible_count} of {comparisonResult.scorable_count} options
                  feasible from <span className="font-mono text-ink-secondary">compare_recovery_options</span>
                  {comparisonResult.recommendation && (
                    <> · recommended: <span className="font-mono text-acid">{comparisonResult.recommendation}</span></>
                  )}
                </p>
              )}
              <button
                className="btn-ghost text-xs w-full"
                onClick={() => dispatch({ type: 'SET_VIEW', view: 'recovery' })}
              >
                Open recovery planner <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
