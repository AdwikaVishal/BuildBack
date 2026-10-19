/**
 * ScheduleView — proposed recovery schedule from generate_recovery_schedule.
 *
 * HARD RULE: every duration shown here is a *relative* unit (the backend
 * never emits calendar dates for this tool). Bars are proportional offsets,
 * labelled with units — never formatted as dates.
 *
 * Everything rendered comes from the ScheduleResult returned by the server;
 * when nothing has been generated yet the view offers to generate it.
 */
import { useMemo, useState } from 'react';
import {
  CalendarClock, AlertTriangle, Layers, ListOrdered, GitMerge,
  ArrowRight, Info, CheckCircle2,
} from 'lucide-react';
import { useApp, useAsyncOp } from '../store/appStore';
import { generateRecoverySchedule } from '../api/mcpClient';
import type { ScheduleResult, ScheduleEntry, ScheduleGroup } from '../types';
import { EmptyState } from './ui/EmptyState';
import { ErrorState } from './ui/ErrorState';
import { Spinner } from './ui/Spinner';
import { MetricCard } from './ui/MetricCard';
import { clsx } from 'clsx';

const GROUP_META: Record<ScheduleGroup, { label: string; dot: string; ring: string; bar: string }> = {
  completed:  { label: 'Completed',  dot: 'bg-ink-muted', ring: 'border-surface-700',     bar: 'bg-ink-muted' },
  ready_now:  { label: 'Ready now',  dot: 'bg-ok',        ring: 'border-ok/40',           bar: 'bg-ok' },
  waiting:    { label: 'Waiting',    dot: 'bg-warn',      ring: 'border-warn/40',         bar: 'bg-warn' },
  blocked:    { label: 'Blocked',    dot: 'bg-fail',      ring: 'border-fail/40',         bar: 'bg-fail' },
};

export function ScheduleView() {
  const [state, dispatch] = useApp();
  const { scheduleResult, planData, selectedOptionId, mcpStatus, disruption } = state;
  const { loading, error, run } = useAsyncOp('schedule');
  const [mode, setMode] = useState<'baseline' | 'option'>('baseline');

  async function handleGenerate(applyOption: boolean) {
    if (!planData) return;
    if (!mcpStatus.connected) return;
    const useOption = applyOption && selectedOptionId != null;
    setMode(useOption ? 'option' : 'baseline');
    const result = await run(() =>
      generateRecoverySchedule(planData.scenario_id as never, {
        ...(useOption && selectedOptionId ? { optionId: selectedOptionId } : {}),
        ...(!useOption && disruption.active ? { disruptedEntityId: disruption.entityId } : {}),
      }),
    );
    if (result) {
      dispatch({ type: 'SET_SCHEDULE', result: result as ScheduleResult });
      dispatch({
        type: 'LOG_ACTIVITY',
        stage: 'schedule_generated',
        label: 'Proposed schedule generated',
        status: 'done',
        detail: `${result.outcome} · ${result.completion?.duration ?? '—'} ${result.completion?.unit ?? ''}`,
      });
    }
  }

  return (
    <div className="space-y-4">
      {/* Generate controls */}
      <div className="card p-4 flex flex-wrap items-center gap-3">
        <CalendarClock className="w-4 h-4 text-acid flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">Proposed recovery schedule</p>
          <p className="text-xs text-ink-muted mt-0.5">
            Relative durations only — this tool never produces calendar dates.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            className="btn-ghost text-xs"
            onClick={() => handleGenerate(false)}
            disabled={loading || !mcpStatus.connected}
            title={mcpStatus.connected ? 'Run generate_recovery_schedule on the current plan' : 'Backend disconnected'}
          >
            {loading && mode === 'baseline' ? <Spinner className="w-3.5 h-3.5" /> : null}
            {disruption.active ? 'Generate (with disruption)' : 'Generate baseline schedule'}
          </button>
          <button
            className="btn-primary text-xs"
            onClick={() => handleGenerate(true)}
            disabled={loading || !mcpStatus.connected || !selectedOptionId}
            title={selectedOptionId ? 'Apply the selected recovery option and schedule' : 'Select a recovery option first'}
          >
            {loading && mode === 'option' ? <Spinner className="w-3.5 h-3.5" /> : <ArrowRight className="w-3.5 h-3.5" />}
            Schedule with selected option
          </button>
        </div>
      </div>

      {error && <ErrorState title="Schedule generation failed" message={error} onRetry={() => handleGenerate(mode === 'option')} />}

      {!scheduleResult && !loading && (
        <EmptyState
          icon={CalendarClock}
          title="No schedule generated yet"
          description="Generate a baseline schedule, or select a recovery option in the Recovery Planner and schedule with it applied."
        />
      )}

      {loading && !scheduleResult && (
        <div className="flex flex-col items-center gap-3 py-16">
          <Spinner className="w-7 h-7 text-acid" />
          <span className="text-sm text-ink-muted">Generating schedule…</span>
        </div>
      )}

      {scheduleResult && <ScheduleReport result={scheduleResult} />}
    </div>
  );
}

// ── Report ────────────────────────────────────────────────────────────────────

function ScheduleReport({ result }: { result: ScheduleResult }) {
  const entries = result.schedule ?? [];
  const unit = result.unit ?? 'units';

  // Relative timeline bounds (numbers, not dates)
  const schedule = result.schedule;
  const bounds = useMemo(() => {
    const ends = (schedule ?? [])
      .map((e) => e.earliest_finish)
      .filter((v): v is number => typeof v === 'number');
    const max = ends.length > 0 ? Math.max(...ends) : 0;
    return { max: max > 0 ? max : 1 };
  }, [schedule]);

  const nameOf = useMemo(
    () => Object.fromEntries((schedule ?? []).map((e) => [e.entity_id, e.name ?? e.entity_id])),
    [schedule],
  );

  return (
    <div className="space-y-4">
      {/* Summary metrics — baseline vs proposed, in relative units */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          label="Baseline duration"
          value={result.baseline?.completion?.duration ?? '—'}
          unit={result.baseline?.completion?.unit ?? result.comparison?.baseline_unit ?? 'units'}
          hint="Baseline schedule computed by generate_recovery_schedule (relative units)"
        />
        <MetricCard
          label="Proposed duration"
          value={result.completion?.duration ?? '—'}
          unit={result.completion?.unit ?? unit}
          tone="info"
          hint="Completion duration of the proposed schedule — relative units, not dates"
        />
        <MetricCard
          label="Duration difference"
          value={result.comparison?.completion_delta != null
            ? `${result.comparison.completion_delta >= 0 ? '+' : ''}${result.comparison.completion_delta}`
            : '—'}
          unit={result.comparison?.comparable ? (result.comparison.proposed_unit ?? unit) : 'not comparable'}
          tone={
            result.comparison?.completion_delta == null
              ? 'neutral'
              : result.comparison.completion_delta > 0 ? 'warn'
              : result.comparison.completion_delta < 0 ? 'ok' : 'neutral'
          }
          hint="Proposed minus baseline completion — only shown when the backend deems the two comparable"
        />
        <MetricCard
          label="Feasibility"
          value={result.outcome === 'scheduled'
            ? (result.feasible ? 'Feasible' : 'Infeasible')
            : result.outcome === 'insufficient_duration_data' ? 'No data' : 'Infeasible'}
          unit={result.violations.length > 0 ? `${result.violations.length} violation(s)` : undefined}
          tone={result.outcome === 'scheduled' && result.feasible ? 'ok' : 'warn'}
          hint="Backend outcome from generate_recovery_schedule"
        />
      </div>

      {/* Outcome banner */}
      {result.outcome !== 'scheduled' ? (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-warn/40 bg-warn/10">
          <AlertTriangle className="w-4 h-4 text-warn flex-shrink-0 mt-0.5" />
          <div className="text-xs space-y-1">
            <p className="text-warn font-semibold">
              {result.outcome === 'insufficient_duration_data'
                ? 'Insufficient duration data'
                : 'Schedule infeasible'}
            </p>
            {result.error && <p className="font-mono text-ink-secondary">{result.error.code}: {result.error.message}</p>}
            {result.missing_data.length > 0 && (
              <p className="text-ink-muted">Missing durations for: {result.missing_data.join(', ')}</p>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="tag-simulated">SIMULATED · PROPOSED</span>
          <span className="text-xs text-ink-muted">
            Completion: <span className="text-ink font-mono">{result.completion?.duration ?? '—'} {result.completion?.unit ?? unit}</span>
            {' '}· relative units, not dates
          </span>
          {result.option_id && (
            <span className="text-xs text-ink-muted font-mono truncate max-w-[280px]">with {result.option_id}</span>
          )}
        </div>
      )}

      {result.violations.length > 0 && (
        <div className="card p-4 border-fail/40">
          <p className="label-tech mb-2">Constraint violations</p>
          {result.violations.map((v, i) => (
            <p key={i} className="text-xs text-fail font-mono">[{v.code}] {v.message}</p>
          ))}
        </div>
      )}

      {/* Critical path + completion coverage — real ScheduleResult fields */}
      {(result.critical_path || result.completion) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {result.critical_path && (
            <div className="card p-4 space-y-2">
              <p className="label-tech">Critical path (proposed)</p>
              <p className="text-xs text-ink-secondary">
                Length:{' '}
                <span className="font-mono text-ink font-bold">
                  {result.critical_path.length} {result.critical_path.unit}
                </span>{' '}
                (relative units)
              </p>
              <div className="flex flex-wrap gap-1">
                {result.critical_path.path.map((id, i) => (
                  <span key={`${id}-${i}`} className="badge bg-acid/5 border border-acid/30 text-ink-secondary font-mono" title={id}>
                    {nameOf[id] ?? id}
                  </span>
                ))}
              </div>
              {result.baseline?.critical_path && (
                <p className="text-[11px] text-ink-muted">
                  Baseline critical path:{' '}
                  <span className="font-mono">
                    {result.baseline.critical_path.path.map((id) => nameOf[id] ?? id).join(' → ')}
                  </span>{' '}
                  ({result.baseline.critical_path.length} {result.baseline.critical_path.unit})
                </p>
              )}
            </div>
          )}

          {result.completion && (
            <div className="card p-4 space-y-2">
              <p className="label-tech">Completion coverage</p>
              <p className="text-xs text-ink-secondary leading-relaxed">{result.completion.explanation}</p>
              <p className={clsx('text-[11px]', result.completion.covers_all_tasks ? 'text-ok' : 'text-warn')}>
                {result.completion.covers_all_tasks
                  ? 'Duration covers all tasks in the plan.'
                  : `Duration excludes ${result.completion.excluded_tasks.length} task(s): ${result.completion.excluded_tasks.join(', ')}`}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Relative timeline bars */}
      {entries.length > 0 && result.outcome === 'scheduled' && (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-acid" />
              <span className="text-sm font-semibold text-ink">Relative Timeline</span>
            </div>
            <span className="text-[11px] text-ink-muted font-mono">0 → {bounds.max} {unit}</span>
          </div>
          <div className="p-4 space-y-1.5 overflow-x-auto">
            {entries.map((entry) => (
              <TimelineRow key={entry.entity_id} entry={entry} max={bounds.max} unit={unit} />
            ))}
          </div>
          <p className="px-4 pb-4 text-[11px] text-ink-muted">
            Bars are offsets in {unit} from the start of the proposed schedule — not wall-clock dates.
          </p>
        </div>
      )}

      {/* Readiness groups */}
      {result.groups && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {(['ready_now', 'waiting', 'blocked', 'completed'] as const).map((g) => {
            const ids = result.groups?.[g] ?? [];
            const meta = GROUP_META[g];
            return (
              <div key={g} className={clsx('card p-3.5 border', meta.ring)}>
                <div className="flex items-center gap-2 mb-2">
                  <span className={clsx('w-2 h-2 rounded-full', meta.dot)} />
                  <span className="label-tech">{meta.label}</span>
                  <span className="ml-auto text-xs font-mono text-ink-secondary">{ids.length}</span>
                </div>
                <ul className="space-y-1">
                  {ids.length === 0 && <li className="text-[11px] text-ink-muted">none</li>}
                  {ids.slice(0, 6).map((id) => (
                    <li key={id} className="text-[11px] text-ink-secondary font-mono truncate" title={id}>
                      {nameOf[id] ?? id}
                    </li>
                  ))}
                  {ids.length > 6 && (
                    <li className="text-[11px] text-ink-muted">+{ids.length - 6} more…</li>
                  )}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      {/* Execution order + parallel layers */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {result.execution_order && result.execution_order.length > 0 && (
          <div className="card">
            <div className="card-header">
              <div className="flex items-center gap-2">
                <ListOrdered className="w-4 h-4 text-info" />
                <span className="text-sm font-semibold text-ink">Execution Order</span>
              </div>
              <span className="text-[11px] text-ink-muted font-mono">{result.execution_order.length} steps</span>
            </div>
            <ol className="p-4 space-y-1.5">
              {result.execution_order.map((id, i) => (
                <li key={`${id}-${i}`} className="flex items-center gap-2.5 text-xs">
                  <span className="w-5 h-5 rounded bg-surface-800 border border-border text-[11px] font-mono text-ink-muted flex items-center justify-center flex-shrink-0">
                    {i + 1}
                  </span>
                  <span className="text-ink-secondary font-mono truncate">{nameOf[id] ?? id}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {result.parallel_groups && result.parallel_groups.length > 0 && (
          <div className="card">
            <div className="card-header">
              <div className="flex items-center gap-2">
                <GitMerge className="w-4 h-4 text-ok" />
                <span className="text-sm font-semibold text-ink">Parallel Layers</span>
              </div>
              <span className="text-[11px] text-ink-muted font-mono">{result.parallel_groups.length} layers</span>
            </div>
            <div className="p-4 space-y-2.5">
              {result.parallel_groups.map((layer) => (
                <div key={layer.layer} className="flex items-start gap-2.5">
                  <span className="text-[11px] font-mono text-ink-muted w-12 flex-shrink-0 pt-1">L{layer.layer}</span>
                  <div className="flex flex-wrap gap-1.5">
                    {layer.entity_ids.map((id) => (
                      <span key={id} className="badge bg-surface-800 border border-border text-ink-secondary text-[11px] font-mono max-w-[180px] truncate" title={id}>
                        {nameOf[id] ?? id}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
              <p className="text-[11px] text-ink-muted pt-1">Tasks in the same layer can run concurrently.</p>
            </div>
          </div>
        )}
      </div>

      {/* Baseline comparison */}
      {result.comparison && (
        <div className="card">
          <div className="card-header">
            <span className="text-sm font-semibold text-ink">Baseline vs Proposed</span>
            {result.comparison.comparable ? (
              <span className="text-[11px] text-ink-muted font-mono">comparable</span>
            ) : (
              <span className="text-[11px] text-warn font-mono">{result.comparison.incomparable_reason}</span>
            )}
          </div>
          <div className="p-4 grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <CompareStat
              label="Completion Δ"
              value={result.comparison.completion_delta != null ? `${result.comparison.completion_delta >= 0 ? '+' : ''}${result.comparison.completion_delta}` : '—'}
              unit={result.comparison.proposed_unit ?? ''}
              tone={result.comparison.completion_delta != null && result.comparison.completion_delta > 0 ? 'warn' : 'ok'}
            />
            <CompareStat
              label="Parallel layers"
              value={result.comparison.parallelism
                ? `${result.comparison.parallelism.baseline_layer_count} → ${result.comparison.parallelism.proposed_layer_count}`
                : '—'}
              tone="neutral"
            />
            <div>
              <p className="label-tech mb-1">Newly unblocked</p>
              {result.comparison.newly_unblocked.length === 0 ? (
                <p className="text-ink-muted text-xs">none</p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {result.comparison.newly_unblocked.map((id) => (
                    <span key={id} className="badge bg-ok/10 text-ok border border-ok/40 text-[11px] font-mono">{nameOf[id] ?? id}</span>
                  ))}
                </div>
              )}
            </div>
            <div>
              <p className="label-tech mb-1">Newly blocked</p>
              {result.comparison.newly_blocked.length === 0 ? (
                <p className="text-ink-muted text-xs">none</p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {result.comparison.newly_blocked.map((id) => (
                    <span key={id} className="badge bg-fail/10 text-fail border border-fail/40 text-[11px] font-mono">{nameOf[id] ?? id}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
          {result.comparison.execution_order_changed && (
            <p className="px-4 pb-3 text-[11px] text-info flex items-center gap-1.5">
              <Info className="w-3 h-3" /> Execution order changed between baseline and proposal.
            </p>
          )}
          {result.comparison.critical_path_changed && (
            <p className="px-4 pb-3 text-[11px] text-warn flex items-center gap-1.5">
              <Info className="w-3 h-3" /> Critical path changed between baseline and proposal.
            </p>
          )}
          {result.comparison.excluded_tasks_changed && (
            <p className="px-4 pb-3 text-[11px] text-warn flex items-center gap-1.5">
              <Info className="w-3 h-3" /> Excluded-task set changed between baseline and proposal.
            </p>
          )}
        </div>
      )}

      {/* Assumptions / limitations */}
      {(result.assumptions.length > 0 || result.limitations.length > 0) && (
        <div className="card p-4 space-y-2">
          {result.assumptions.length > 0 && (
            <div>
              <p className="label-tech mb-1">Assumptions</p>
              {result.assumptions.map((a, i) => (
                <p key={i} className="text-[11px] text-ink-muted flex items-start gap-1.5">
                  <CheckCircle2 className="w-3 h-3 text-ink-muted flex-shrink-0 mt-0.5" /> {a}
                </p>
              ))}
            </div>
          )}
          {result.limitations.length > 0 && (
            <div>
              <p className="label-tech mb-1">Limitations</p>
              {result.limitations.map((l, i) => (
                <p key={i} className="text-[11px] text-ink-muted flex items-start gap-1.5">
                  <AlertTriangle className="w-3 h-3 text-warn flex-shrink-0 mt-0.5" /> {l}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TimelineRow({ entry, max, unit }: { entry: ScheduleEntry; max: number; unit: string }) {
  const hasTimes = entry.earliest_start != null && entry.earliest_finish != null;
  const left = hasTimes ? (entry.earliest_start! / max) * 100 : 0;
  const width = hasTimes
    ? Math.max(((entry.earliest_finish! - entry.earliest_start!) / max) * 100, 1.5)
    : 100;
  const meta = GROUP_META[entry.group];

  return (
    <div className="flex items-center gap-3 min-w-[520px]">
      <div className="w-56 flex-shrink-0 flex items-center gap-1.5 min-w-0">
        <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', meta.dot)} />
        <span className="text-[13px] text-ink-secondary truncate" title={entry.name ?? entry.entity_id}>
          {entry.name ?? entry.entity_id}
        </span>
        {entry.on_critical_path && (
          <span className="text-[11px] text-warn font-bold flex-shrink-0" title="On critical path">CP</span>
        )}
      </div>

      <div className="relative flex-1 h-6 bg-surface-900 rounded border border-border overflow-hidden">
        {hasTimes ? (
          <div
            className={clsx('absolute top-0.5 bottom-0.5 rounded-[3px]', meta.bar)}
            style={{ left: `${left}%`, width: `${width}%`, opacity: 0.85 }}
            title={`${entry.earliest_start} → ${entry.earliest_finish} ${unit} (relative)`}
          />
        ) : (
          <div className="absolute inset-0 flex items-center px-2 text-[11px] text-ink-muted">
            no duration data
          </div>
        )}
      </div>

      <span className="w-32 flex-shrink-0 text-right text-[11px] font-mono text-ink-muted">
        {hasTimes
          ? `${entry.earliest_start}–${entry.earliest_finish} ${unit}`
          : '—'}
      </span>
      <span
        className="w-4 flex-shrink-0 text-ink-muted cursor-help"
        title={[
          entry.reason,
          entry.waiting_time != null ? `Waiting time: ${entry.waiting_time} ${unit}` : null,
          entry.hard_prerequisites.length > 0 ? `Hard prerequisites: ${entry.hard_prerequisites.join(', ')}` : null,
        ].filter(Boolean).join('\n') || 'No extra detail'}
      >
        <Info className="w-3.5 h-3.5" />
      </span>
    </div>
  );
}

function CompareStat({ label, value, unit, tone }: {
  label: string; value: string; unit?: string;
  tone: 'ok' | 'warn' | 'neutral';
}) {
  return (
    <div>
      <p className="label-tech mb-1">{label}</p>
      <p className={clsx(
        'text-2xl font-bold font-mono leading-none',
        tone === 'ok' ? 'text-ok' : tone === 'warn' ? 'text-warn' : 'text-ink',
      )}>
        {value}
        {unit && <span className="text-[11px] text-ink-muted ml-1">{unit}</span>}
      </p>
    </div>
  );
}
