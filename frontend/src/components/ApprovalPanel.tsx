import { useState } from 'react';
import { CheckCircle2, XCircle, AlertTriangle, ShieldCheck, Info } from 'lucide-react';
import { useApp, useAsyncOp } from '../store/appStore';
import { evaluateRecoveryPlan } from '../api/mcpClient';
import { MOCK_EVALUATION_RESULT } from '../data/mockScenario';
import type { EvaluationResult } from '../types';
import { EmptyState } from './ui/EmptyState';
import { EvidencePath } from './ui/EvidencePath';
import { Spinner } from './ui/Spinner';
import { clsx } from 'clsx';

export function ApprovalPanel() {
  const [state, dispatch] = useApp();
  const {
    selectedOptionId,
    planData,
    evaluationResult,
    comparisonResult,
    approvalConfirmed,
    commitError,
    mcpStatus,
  } = state;

  const { loading: evalLoading, error: evalError, run: runEval } = useAsyncOp('evaluate');
  const [confirmed, setConfirmed] = useState(false);

  if (!selectedOptionId) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="No option selected"
        description="Choose a recovery option from the Recovery Options panel first."
      />
    );
  }

  // Find the ranked entry for this option
  const rankedEntry = comparisonResult?.ranked.find((e) => e.option_id === selectedOptionId)
    ?? comparisonResult?.excluded.find((e) => e.option_id === selectedOptionId);

  async function handleEvaluate() {
    if (!planData) return;
    const result = await runEval(async () => {
      if (!mcpStatus.connected) {
        // Deterministic mock — structurally valid result
        return MOCK_EVALUATION_RESULT;
      }
      return evaluateRecoveryPlan(
        planData.scenario_id as never,
        selectedOptionId!,
        planData.version,
      );
    });
    if (result) {
      dispatch({ type: 'SET_EVALUATION', result: result as EvaluationResult });
    }
  }

  function handleConfirm() {
    // The backend has no write path yet. We show the evaluation verdict and
    // inform the user honestly that no commit is executed.
    setConfirmed(true);
    dispatch({ type: 'CONFIRM_APPROVAL' });
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent-blue" />
        <h3 className="text-sm font-medium text-gray-300">Review & Approve</h3>
      </div>

      {/* Option summary */}
      <div className="card p-4 space-y-2">
        <p className="text-[10px] text-gray-600 uppercase tracking-wide">Selected Option</p>
        <p className="text-xs font-mono text-gray-300 break-all">{selectedOptionId}</p>
        {rankedEntry && (
          <div className="flex gap-4 text-xs mt-2">
            {rankedEntry.rank != null && (
              <span className="text-gray-500">Rank: <span className="text-amber-400 font-bold">#{rankedEntry.rank}</span></span>
            )}
            {rankedEntry.score != null && (
              <span className="text-gray-500">Score: <span className="text-gray-300 font-mono">{rankedEntry.score.toFixed(2)}</span></span>
            )}
            {rankedEntry.forecast?.delay_delta != null && (
              <span className="text-gray-500">
                Δ Delay: <span className={clsx('font-mono', rankedEntry.forecast.delay_delta > 0 ? 'text-yellow-400' : 'text-emerald-400')}>
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
          <span className="text-sm font-medium text-gray-300">Structural Evaluation</span>
          {!evaluationResult && (
            <button
              className="btn-ghost text-xs"
              onClick={handleEvaluate}
              disabled={evalLoading}
            >
              {evalLoading ? <><Spinner className="w-3.5 h-3.5" /> Evaluating…</> : 'Evaluate'}
            </button>
          )}
        </div>

        {evalLoading && (
          <div className="flex items-center gap-2 p-4 text-sm text-gray-500">
            <Spinner className="w-4 h-4" /> Running structural check…
          </div>
        )}

        {evalError && (
          <div className="p-4 text-xs text-red-400">{evalError}</div>
        )}

        {evaluationResult && (
          <EvaluationDetail result={evaluationResult} />
        )}

        {!evaluationResult && !evalLoading && (
          <div className="p-4 text-xs text-gray-600">
            Click "Evaluate" to run a structural check of this option against the committed plan.
          </div>
        )}
      </div>

      {/* Business readiness warning — always shown per data contract */}
      <div className="flex items-start gap-3 p-3 bg-amber-900/20 border border-amber-800 rounded-lg">
        <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
        <p className="text-xs text-amber-300">
          <strong>Structural validity ≠ business readiness.</strong>{' '}
          Removing a hard prerequisite may break integration contracts. Have a human review this before committing.
        </p>
      </div>

      {/* Commit section */}
      {!approvalConfirmed && !confirmed && (
        <div className="card p-4 space-y-3">
          <p className="text-xs text-gray-400 font-medium">Commit to Plan</p>
          <p className="text-xs text-gray-600">
            The RippleBack backend is currently <strong>read-only</strong> — no writes are implemented.
            Clicking Confirm records your approval intent but does not modify the committed plan.
          </p>
          <div className="flex gap-2">
            <button
              className="btn-success text-xs"
              onClick={handleConfirm}
              disabled={!evaluationResult || evalLoading}
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              Confirm Approval
            </button>
            <button
              className="btn-ghost text-xs"
              onClick={() => dispatch({ type: 'SELECT_OPTION', optionId: '' })}
            >
              <XCircle className="w-3.5 h-3.5" />
              Cancel
            </button>
          </div>
          {!evaluationResult && (
            <p className="text-[10px] text-gray-600">Run structural evaluation first to enable approval.</p>
          )}
        </div>
      )}

      {/* Success state */}
      {(approvalConfirmed || confirmed) && (
        <div className="flex items-start gap-3 p-4 bg-emerald-900/20 border border-emerald-800 rounded-lg">
          <CheckCircle2 className="w-5 h-5 text-emerald-400 flex-shrink-0" />
          <div className="text-xs space-y-1">
            <p className="text-emerald-300 font-medium">Approval recorded</p>
            <p className="text-gray-500">
              The backend is read-only. No change was committed. To commit, implement a write
              endpoint in <span className="font-mono">rippleback/server.py</span> and update the proxy.
            </p>
            <p className="text-gray-600 font-mono text-[10px]">Option: {selectedOptionId}</p>
          </div>
        </div>
      )}

      {commitError && (
        <div className="flex items-start gap-3 p-4 bg-red-900/20 border border-red-900 rounded-lg">
          <XCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
          <div className="text-xs">
            <p className="text-red-300 font-medium">Commit failed</p>
            <p className="text-red-500 mt-1">{commitError}</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Evaluation detail ─────────────────────────────────────────────────────────

function EvaluationDetail({ result }: { result: EvaluationResult }) {
  const levelIcon: Record<string, React.ReactNode> = {
    info:    <Info       className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />,
    warning: <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />,
    error:   <XCircle    className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />,
  };

  return (
    <div className="p-4 space-y-4">
      {/* Verdict */}
      <div className="flex items-center gap-2">
        {result.verdict === 'structurally_valid' ? (
          <CheckCircle2 className="w-5 h-5 text-emerald-400" />
        ) : (
          <XCircle className="w-5 h-5 text-red-400" />
        )}
        <span className={clsx('text-sm font-medium', result.verdict === 'structurally_valid' ? 'text-emerald-300' : 'text-red-300')}>
          {result.verdict === 'structurally_valid' ? 'Structurally valid' : 'Rejected'}
        </span>
        <span className="text-xs text-gray-600 font-mono ml-auto">v{result.plan_version}</span>
      </div>

      {/* Findings */}
      <div className="space-y-2">
        {result.findings.map((finding, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            {levelIcon[finding.level]}
            <span className="text-gray-400">{finding.message}</span>
          </div>
        ))}
      </div>

      {/* Impact */}
      {result.impact && (
        <div className="space-y-2 border-t border-surface-600 pt-3">
          <p className="text-[10px] text-gray-600 uppercase tracking-wide">Impact (residual graph)</p>
          <div className="text-xs space-y-1.5">
            <div className="flex gap-2">
              <span className="text-gray-500">Unblocks:</span>
              <div className="flex flex-wrap gap-1">
                {result.impact.unblocks.map((id) => (
                  <span key={id} className="font-mono text-emerald-400 bg-emerald-900/20 rounded px-1">{id}</span>
                ))}
                {result.impact.unblocks.length === 0 && <span className="text-gray-600">none</span>}
              </div>
            </div>
            {result.impact.still_blocked.length > 0 && (
              <div className="flex gap-2">
                <span className="text-gray-500">Still blocked:</span>
                <div className="flex flex-wrap gap-1">
                  {result.impact.still_blocked.map((id) => (
                    <span key={id} className="font-mono text-red-400 bg-red-900/20 rounded px-1">{id}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="text-xs">
            <span className="text-gray-500">Removed edge: </span>
            <EvidencePath
              path={[result.impact.removed_dependency.prerequisite, result.impact.removed_dependency.dependent]}
            />
          </div>
        </div>
      )}
    </div>
  );
}
