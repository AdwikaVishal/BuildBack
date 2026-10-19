/**
 * OverviewView — editorial landing page for the control center.
 *
 * Every number here is derived from real backend results already in the
 * store. When a value is unknown (backend offline, no disruption run yet)
 * it renders '—' — never an invented figure.
 */
import {
  Zap, ArrowRight, GitBranch, Radio, LifeBuoy, CalendarClock, WifiOff,
} from 'lucide-react';
import { useApp } from '../store/appStore';
import { MetricCard } from './ui/MetricCard';
import { EmptyState } from './ui/EmptyState';
import { StatusBadge } from './ui/StatusBadge';
import { scenarioLabel } from '../data/scenarios';

export function OverviewView() {
  const [state, dispatch] = useApp();
  const {
    activeScenarioId, scenarios, planData, planError, disruption, triageResult,
    comparisonResult, scheduleResult, mcpStatus, selectedOptionId,
  } = state;

  const activeSummary = scenarios.find((s) => s.scenario_id === activeScenarioId);
  const activeLabel = scenarioLabel(activeScenarioId, activeSummary);
  const connected = mcpStatus.connected;

  const blocked = triageResult?.summary.blocked;
  const atRisk = triageResult?.summary.at_risk;
  const feasible = comparisonResult?.feasible_count;
  const recommendation = comparisonResult?.recommendation ?? null;
  const scheduleDuration = scheduleResult?.completion?.duration;
  const scheduleUnit = scheduleResult?.completion?.unit ?? 'units';

  return (
    <div className="space-y-5">
      {/* ── Hero ── */}
      <section className="relative overflow-hidden rounded-card border border-surface-700 bg-surface-850 orb-accent">
        <div className="absolute inset-0 grid-accent" aria-hidden="true" />
        <div className="relative px-5 sm:px-8 py-6 sm:py-8">
          <p className="label-tech mb-3">Recovery Control · {activeLabel}</p>
          <h2 className="font-display font-bold text-ink leading-[0.95] tracking-tight text-3xl sm:text-4xl xl:text-5xl">
            WHEN SYSTEMS FAIL.
            <br />
            <span className="text-acid">FIND A WAY BACK.</span>
          </h2>
          <p className="text-sm text-ink-secondary mt-3 max-w-xl leading-relaxed">
            Simulate a disruption, trace its blast radius through the dependency graph, and review
            backend-ranked recovery options — all hypothetical, read-only, and clearly labelled.
          </p>

          {!connected && (
            <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-fail/40 bg-fail/5 px-3.5 py-2.5 max-w-xl">
              <WifiOff className="w-4 h-4 text-fail flex-shrink-0 mt-0.5" />
              <p className="text-xs text-ink-secondary leading-relaxed">
                <strong className="text-fail">Backend unreachable.</strong> Start{' '}
                <span className="font-mono text-ink">python -m rippleback.server</span>, then{' '}
                <span className="font-mono text-ink">python -m rippleback.adapter</span>. The dashboard
                shows no analysis data until it reconnects — no placeholder results.
              </p>
            </div>
          )}

          <div className="flex flex-wrap gap-2.5 mt-5">
            <button className="btn-primary" onClick={() => dispatch({ type: 'SET_VIEW', view: 'disruptions' })}>
              <Zap className="w-4 h-4" />
              Simulate a disruption
            </button>
            <button className="btn-ghost" onClick={() => dispatch({ type: 'SET_VIEW', view: 'graph' })}>
              <GitBranch className="w-4 h-4" />
              Open dependency graph
            </button>
          </div>
        </div>
      </section>

      {/* ── Metrics ── */}
      {!planData && planError && (
        <div className="flex items-start gap-2.5 rounded-lg border border-fail/40 bg-fail/5 px-3.5 py-2.5">
          <WifiOff className="w-4 h-4 text-fail flex-shrink-0 mt-0.5" />
          <p className="text-xs text-ink-secondary leading-relaxed">
            <strong className="text-fail">Plan failed to load:</strong>{' '}
            <span className="font-mono text-ink">{planError}</span>{' '}
            Metrics below show '—' until the backend returns a plan.
          </p>
        </div>
      )}
      <section className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-5 gap-3" aria-label="Key metrics">
        <MetricCard
          label="Plan entities"
          value={planData ? planData.entities.length : '—'}
          hint={planData ? `${planData.dependencies.length} dependencies · plan v${planData.version}` : 'Plan not loaded'}
        />
        <MetricCard
          label="Blocked now"
          value={blocked ?? '—'}
          tone={blocked != null && blocked > 0 ? 'fail' : 'neutral'}
          unit={blocked != null ? undefined : 'no triage run'}
          hint="From analyze_blocked_tasks after a simulated disruption"
        />
        <MetricCard
          label="At risk"
          value={atRisk ?? '—'}
          tone={atRisk != null && atRisk > 0 ? 'warn' : 'neutral'}
          unit={atRisk != null ? undefined : 'no triage run'}
          hint="Triage entities flagged at_risk by the backend"
        />
        <MetricCard
          label="Feasible options"
          value={feasible ?? '—'}
          tone={feasible != null && feasible > 0 ? 'ok' : 'neutral'}
          unit={feasible != null ? undefined : 'no comparison run'}
          hint="From compare_recovery_options — backend-ranked"
        />
        <MetricCard
          label="Schedule duration"
          value={scheduleDuration ?? '—'}
          unit={scheduleDuration != null ? `${scheduleUnit} (relative)` : 'no schedule yet'}
          tone="info"
          hint="generate_recovery_schedule — relative units, never calendar dates"
        />
      </section>

      {/* ── Active disruption + recovery overview ── */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Active disruption */}
        <div className="card lg:col-span-2">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-warn" />
              <span className="text-sm font-semibold text-ink">Active Disruption</span>
            </div>
            {disruption.active && <span className="tag-simulated">SIMULATED</span>}
          </div>
          <div className="p-4">
            {disruption.active ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <StatusBadge status="blocked" />
                  <span className="text-sm text-ink font-medium">{disruption.entityId}</span>
                  <span className="badge bg-surface-800 border border-surface-700 text-ink-muted">
                    {disruption.severity} · {disruption.eventType}
                  </span>
                </div>
                <p className="text-xs text-ink-secondary leading-relaxed">{disruption.reason}</p>
                {triageResult && (
                  <div className="flex flex-wrap gap-2 text-xs">
                    {(['blocked', 'external', 'at_risk', 'healthy', 'done'] as const).map((k) => (
                      <span key={k} className="flex items-center gap-1.5 text-ink-muted">
                        <StatusBadge status={k} showDot />
                        <span className="font-mono text-ink-secondary">{triageResult.summary[k] ?? 0}</span>
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap gap-2 pt-1">
                  <button className="btn-ghost text-xs" onClick={() => dispatch({ type: 'SET_VIEW', view: 'impact' })}>
                    View impact <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                  <button className="btn-ghost text-xs" onClick={() => dispatch({ type: 'SET_VIEW', view: 'recovery' })}>
                    <LifeBuoy className="w-3.5 h-3.5" /> Recovery options
                  </button>
                </div>
              </div>
            ) : (
              <EmptyState
                icon={Zap}
                title="No disruption active"
                description="Run a simulation from the Disruptions panel to see triage, impact, and ranked recovery options."
                action={
                  <button className="btn-primary text-xs" onClick={() => dispatch({ type: 'SET_VIEW', view: 'disruptions' })}>
                    Go to simulator
                  </button>
                }
              />
            )}
          </div>
        </div>

        {/* Recovery / schedule snapshot */}
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <CalendarClock className="w-4 h-4 text-info" />
              <span className="text-sm font-semibold text-ink">Recovery Snapshot</span>
            </div>
          </div>
          <div className="p-4 space-y-3 text-xs">
            {recommendation ? (
              <>
                <div>
                  <p className="label-tech mb-1">Backend recommendation</p>
                  <p className="font-mono text-ink-secondary break-all leading-relaxed">{recommendation}</p>
                </div>
                <div>
                  <p className="label-tech mb-1">Selected option</p>
                  <p className="font-mono text-ink-secondary break-all leading-relaxed">
                    {selectedOptionId ?? '— none selected —'}
                  </p>
                </div>
              </>
            ) : (
              <p className="text-ink-muted">No recovery options generated yet.</p>
            )}
            {scheduleResult && (
              <div>
                <p className="label-tech mb-1">Proposed schedule</p>
                <p className="text-ink-secondary">
                  {scheduleResult.outcome === 'scheduled'
                    ? `Scheduled in ${scheduleResult.completion?.duration ?? '—'} ${scheduleUnit} (relative)`
                    : scheduleResult.outcome}
                  {scheduleResult.option_id ? ' · with selected option' : ' · baseline'}
                </p>
              </div>
            )}
            <button
              className="btn-ghost text-xs w-full mt-1"
              onClick={() => dispatch({ type: 'SET_VIEW', view: 'timeline' })}
            >
              Open schedule view <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
