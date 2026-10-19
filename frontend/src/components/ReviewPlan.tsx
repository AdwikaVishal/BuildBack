/**
 * ReviewPlan — honest review of a *proposed* recovery plan.
 *
 * The RippleBack backend is read-only: there is no approval or commit flow.
 * This view therefore:
 *  - shows the selected simulated option and its backend-ranked metrics,
 *  - runs the real structural evaluation tool (no mock fallback),
 *  - can generate a proposed recovery schedule (generate_recovery_schedule),
 *  - states plainly that nothing is approved or committed.
 */
import { CheckCircle2, XCircle, AlertTriangle, FileSearch, Info, CalendarClock, BarChart2, Zap } from 'lucide-react';
import { useApp, useAsyncOp } from '../store/appStore';
import { evaluateRecoveryPlan, generateRecoverySchedule } from '../api/mcpClient';
import type { EvaluationResult, ScheduleResult } from '../types';
import { EmptyState } from './ui/EmptyState';
import { ErrorState } from './ui/ErrorState';
import { EvidencePath } from './ui/EvidencePath';
import { Spinner } from './ui/Spinner';
import { clsx } from 'clsx';

export function ReviewPlan() {
  const [state, dispatch] = useApp();
  const {
    selectedOptionId,
    planData,
    evaluationResult,
    comparisonResult,
    scheduleResult,
    mcpStatus,
  } = state;

  const { loading: evalLoading, error: evalError, run: runEval } = useAsyncOp('evaluate');
  const { loading: schedLoading, error: schedError, run: runSchedule } = useAsyncOp('schedule');

  if (!selectedOptionId) {
    return (
      <EmptyState
        icon={FileSearch}
        title="No option selected"
        description={
          comparisonResult
            ? 'Pick a ranked recovery option to review its structural evaluation and proposed schedule. Nothing here is approved or committed — this is a read-only review.'
            : 'Run a disruption simulation first to generate ranked recovery options, then pick one to review.'
        }
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <button
              className="btn-primary text-xs px-4 py-2"
              onClick={() => dispatch({ type: 'SET_VIEW', view: 'recovery' })}
            >
              <BarChart2 className="w-3.5 h-3.5" />
              {comparisonResult ? 'Open recovery options' : 'Open Recovery Planner'}
            </button>
            {!comparisonResult && (
              <button
                className="btn-ghost text-xs px-4 py-2"
                onClick={() => dispatch({ type: 'SET_VIEW', view: 'disruptions' })}
              >
                <Zap className="w-3.5 h-3.5" />
                Run a simulation
              </button>
            )}
          </div>
        }
      />
    );
  }

  // Find the ranked entry for this option
  const rankedEntry = comparisonResult?.ranked.find((e) => e.option_id === selectedOptionId)
    ?? comparisonResult?.excluded.find((e) => e.option_id === selectedOptionId);

  async function handleEvaluate() {
    if (!planData) return;
    if (!mcpStatus.connected) return;
    const result = await runEval(() =>
      evaluateRecoveryPlan(planData.scenario_id as never, selectedOptionId!, planData.version),
    );
    if (result) {
      dispatch({ type: 'SET_EVALUATION', result: result as EvaluationResult });
    }
  }

  async function handleGenerateSchedule() {
    if (!planData) return;
    if (!mcpStatus.connected) return;
    const result = await runSchedule(() =>
      generateRecoverySchedule(planData.scenario_id as never, { optionId: selectedOptionId! }),
    );
    if (result) {
      dispatch({ type: 'SET_SCHEDULE', result: result as ScheduleResult });
      dispatch({ type: 'LOG_ACTIVITY', stage: 'schedule_generated', label: 'Proposed schedule generated', status: 'done', detail: selectedOptionId! });
      dispatch({ type: 'SET_VIEW', view: 'timeline' });
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <FileSearch className="w-4 h-4 text-acid" />
        <h3 className="text-sm font-medium text-ink">Review Proposed Plan</h3>
        <span className="tag-simulated text-[11px]">SIMULATED</span>
      </div>

      {/* Read-only statement — always visible */}
      <div className="flex items-start gap-3 p-3 bg-info/10 border border-info/30 rounded-lg">
        <Info className="w-4 h-4 text-info flex-shrink-0 mt-0.5" />
        <p className="text-xs text-ink-secondary">
          The backend is <strong className="text-ink">read-only</strong>. This is a review of a
          simulated option — nothing is approved, committed, or written back to the plan.
        </p>
      </div>

      {/* Option summary */}
      <div className="card p-4 space-y-2">
        <p className="text-[11px] text-ink-muted uppercase tracking-wide">Selected Option</p>
        <p className="text-xs font-mono text-ink-secondary break-all">{selectedOptionId}</p>
        {rankedEntry && (
          <div className="flex gap-4 text-xs mt-2 flex-wrap">
            {rankedEntry.rank != null && (
              <span className="text-ink-muted">Rank: <span className="text-acid font-bold">#{rankedEntry.rank}</span></span>
            )}
            {rankedEntry.score != null && (
              <span className="text-ink-muted">Score: <span className="text-ink-secondary font-mono">{rankedEntry.score.toFixed(2)}</span></span>
            )}
            {rankedEntry.forecast?.delay_delta != null && (
              <span className="text-ink-muted">
                Δ Delay: <span className={clsx('font-mono', rankedEntry.forecast.delay_delta > 0 ? 'text-warn' : 'text-ok')}>
                  {rankedEntry.forecast.delay_delta >= 0 ? '+' : ''}{rankedEntry.forecast.delay_delta} {rankedEntry.forecast.unit}
                </span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Structural evaluation */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-medium text-ink">Structural Evaluation</span>
          <div className="flex items-center gap-2">
            {evaluationResult && (
              <span className="text-[11px] text-ink-muted font-mono">evaluated on v{evaluationResult.plan_version}</span>
            )}
            <button
              className="btn-ghost text-xs"
              onClick={handleEvaluate}
              disabled={evalLoading || !mcpStatus.connected}
            >
              {evalLoading
                ? <><Spinner className="w-3.5 h-3.5" /> Evaluating…</>
                : evaluationResult ? 'Re-evaluate' : 'Evaluate'}
            </button>
          </div>
        </div>

        {evalLoading && (
          <div className="flex items-center gap-2 p-4 text-sm text-ink-muted">
            <Spinner className="w-4 h-4 text-acid" /> Running structural check…
          </div>
        )}

        {evalError && (
          <div className="p-4"><ErrorState title="Evaluation failed" message={evalError} onRetry={handleEvaluate} /></div>
        )}

        {evaluationResult && (
          <EvaluationDetail result={evaluationResult} />
        )}

        {!evaluationResult && !evalLoading && !evalError && (
          <div className="p-4 text-xs text-ink-muted">
            {mcpStatus.connected
              ? 'Click "Evaluate" to run a structural check of this option against the committed plan.'
              : 'MCP backend is disconnected — connect to run a structural check. No mock results are shown.'}
          </div>
        )}
      </div>

      {/* Proposed recovery schedule */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-medium text-ink">Proposed Recovery Schedule</span>
          <button
            className="btn-ghost text-xs"
            onClick={handleGenerateSchedule}
            disabled={schedLoading || !mcpStatus.connected}
          >
            {schedLoading
              ? <><Spinner className="w-3.5 h-3.5" /> Generating…</>
              : <><CalendarClock className="w-3.5 h-3.5" /> Generate schedule</>}
          </button>
        </div>
        <div className="p-4 text-xs text-ink-muted space-y-1.5">
          <p>
            Runs <span className="font-mono text-ink-secondary">generate_recovery_schedule</span> with this
            option applied and shows readiness groups, parallel layers, and the execution order.
          </p>
          <p className="text-[11px] text-ink-muted">
            Durations are <strong className="text-ink-secondary">relative units</strong> — never calendar dates.
          </p>
          {schedError && <ErrorState title="Schedule generation failed" message={schedError} onRetry={handleGenerateSchedule} />}
          {scheduleResult && !schedLoading && (
            <button className="btn-primary text-xs mt-1" onClick={() => dispatch({ type: 'SET_VIEW', view: 'timeline' })}>
              Open schedule view
            </button>
          )}
        </div>
      </div>

      {/* Business readiness warning — always shown per data contract */}
      <div className="flex items-start gap-3 p-3 bg-warn/10 border border-warn/30 rounded-lg">
        <AlertTriangle className="w-4 h-4 text-warn flex-shrink-0 mt-0.5" />
        <p className="text-xs text-warn">
          <strong>Structural validity ≠ business readiness.</strong>{' '}
          Removing a hard prerequisite may break integration contracts. Have a human review this before any change is applied.
        </p>
      </div>

      {/* Clear next steps instead of an approval button */}
      <div className="card p-4 space-y-2">
        <p className="text-xs text-ink font-medium">Next steps</p>
        <ul className="text-xs text-ink-secondary space-y-1 list-disc list-inside">
          <li>Run the structural evaluation above against the committed plan.</li>
          <li>Generate the proposed recovery schedule to see readiness and parallel work.</li>
          <li>Export / hand this proposal to the team that owns the plan — the dashboard cannot commit changes.</li>
        </ul>
        <button
          className="btn-ghost text-xs mt-1"
          onClick={() => dispatch({ type: 'SELECT_OPTION', optionId: '' })}
        >
          <XCircle className="w-3.5 h-3.5" />
          Clear selection
        </button>
      </div>
    </div>
  );
}

// ── Evaluation detail ─────────────────────────────────────────────────────────

function EvaluationDetail({ result }: { result: EvaluationResult }) {
  const levelIcon: Record<string, React.ReactNode> = {
    info:    <Info       className="w-3.5 h-3.5 text-info flex-shrink-0" />,
    warning: <AlertTriangle className="w-3.5 h-3.5 text-warn flex-shrink-0" />,
    error:   <XCircle    className="w-3.5 h-3.5 text-fail flex-shrink-0" />,
  };

  return (
    <div className="p-4 space-y-4">
      {/* Verdict */}
      <div className="flex items-center gap-2">
        {result.verdict === 'structurally_valid' ? (
          <CheckCircle2 className="w-5 h-5 text-ok" />
        ) : (
          <XCircle className="w-5 h-5 text-fail" />
        )}
        <span className={clsx('text-sm font-medium', result.verdict === 'structurally_valid' ? 'text-ok' : 'text-fail')}>
          {result.verdict === 'structurally_valid' ? 'Structurally valid' : 'Rejected'}
        </span>
        <span className="text-xs text-ink-muted font-mono ml-auto">v{result.plan_version}</span>
      </div>

      {/* Version-mismatch honesty: evaluated against an older/newer plan version */}
      {result.expected_plan_version !== result.plan_version && (
        <div className="flex items-start gap-2 p-2.5 bg-warn/10 border border-warn/30 rounded-lg text-xs text-warn">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <span>
            Evaluated against expected plan v{result.expected_plan_version}, but the result reports v{result.plan_version}.
            Re-run the evaluation against the current plan.
          </span>
        </div>
      )}

      {/* Findings */}
      <div className="space-y-2">
        {result.findings.map((finding, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            {levelIcon[finding.level]}
            <span className="text-ink-secondary">{finding.message}</span>
          </div>
        ))}
      </div>

      {/* Impact */}
      {result.impact && (
        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-[11px] text-ink-muted uppercase tracking-wide">Impact (residual graph)</p>
          <div className="text-xs space-y-1.5">
            <div className="flex gap-2 flex-wrap">
              <span className="text-ink-muted">Unblocks:</span>
              <div className="flex flex-wrap gap-1">
                {result.impact.unblocks.map((id) => (
                  <span key={id} className="font-mono text-ok bg-ok/15 rounded px-1">{id}</span>
                ))}
                {result.impact.unblocks.length === 0 && <span className="text-ink-muted">none</span>}
              </div>
            </div>
            {result.impact.still_blocked.length > 0 && (
              <div className="flex gap-2 flex-wrap">
                <span className="text-ink-muted">Still blocked:</span>
                <div className="flex flex-wrap gap-1">
                  {result.impact.still_blocked.map((id) => (
                    <span key={id} className="font-mono text-fail bg-fail/15 rounded px-1">{id}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="text-xs">
            <span className="text-ink-muted">Removed edge: </span>
            <EvidencePath
              path={[result.impact.removed_dependency.prerequisite, result.impact.removed_dependency.dependent]}
            />
          </div>
        </div>
      )}
    </div>
  );
}
