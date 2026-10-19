/**
 * RecoveryOptions — decision-making workspace for ranked recovery options.
 *
 * Improvements:
 *  - Human-readable option labels (parsed from drop-dependency:X->Y format)
 *  - Change summary (what edge is removed, what gets unblocked)
 *  - Before/after path diff in expanded view
 *  - Clear recommended treatment
 *  - Side-by-side compare mode
 *  - Infeasible options with violation reasons
 *  - Backend-only ranking — no frontend scoring
 *
 * The backend is read-only: selecting an option opens a *review* of the
 * simulated plan. Nothing is approved or committed from this UI.
 */
import { useState } from 'react';
import {
  Trophy, BarChart2, Info, ChevronDown, ChevronUp,
  ArrowRight, XCircle, Sparkles, Zap,
} from 'lucide-react';
import type { ComparisonResult, RankedEntry, RecoveryOption, RecoveryResult } from '../types';
import { EmptyState } from './ui/EmptyState';
import { ErrorState } from './ui/ErrorState';
import { Spinner } from './ui/Spinner';
import { clsx } from 'clsx';
import { useApp } from '../store/appStore';

interface Props {
  comparisonResult: ComparisonResult | null;
  recoveryResult: RecoveryResult | null;
  loading?: boolean;
  error?: string | null;
}

/** Parse "drop-dependency:dependent->prerequisite" into readable text */
function parseOptionId(optionId: string): { dependent: string; prerequisite: string } | null {
  const m = optionId.match(/^drop-dependency:(.+)->(.+)$/);
  if (!m) return null;
  return { dependent: m[1], prerequisite: m[2] };
}

function OptionLabel({ optionId }: { optionId: string }) {
  const parsed = parseOptionId(optionId);
  if (!parsed) {
    return <span className="font-mono text-ink-secondary truncate text-xs">{optionId}</span>;
  }
  return (
    <span className="text-xs text-ink-secondary">
      Remove dependency: <span className="font-mono text-info">{parsed.dependent}</span>
      {' '}no longer requires{' '}
      <span className="font-mono text-fail">{parsed.prerequisite}</span>
    </span>
  );
}

export function RecoveryOptions({ comparisonResult, recoveryResult, loading, error }: Props) {
  const [state, dispatch] = useApp();
  const { selectedOptionId } = state;
  const [compareMode, setCompareMode] = useState(false);
  // Expansion state: until the user interacts, a single ranked option is
  // auto-expanded so its detail isn't hidden behind an undiscovered card.
  const [userExpanded, setUserExpanded] = useState<string | null>(null);
  const [userToggled, setUserToggled] = useState(false);

  const ranked = comparisonResult?.ranked ?? [];
  const expandedId = userToggled
    ? userExpanded
    : ranked.length === 1
      ? ranked[0].option_id
      : null;

  function handleToggle(optionId: string) {
    setUserToggled(true);
    setUserExpanded(expandedId === optionId ? null : optionId);
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <Spinner className="w-7 h-7 text-acid" />
        <span className="text-sm text-ink-muted">Generating recovery options…</span>
      </div>
    );
  }

  if (error) {
    return <ErrorState title="Could not generate options" message={error} />;
  }

  if (!comparisonResult && !recoveryResult) {
    return (
      <EmptyState
        icon={BarChart2}
        title="No recovery options yet"
        description="Recovery options are generated from a real disruption simulation. Trigger a disruption to see ranked, backend-scored options here — nothing on this page is fabricated."
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <button
              className="btn-primary text-xs px-4 py-2"
              onClick={() => dispatch({ type: 'SET_VIEW', view: 'disruptions' })}
            >
              <Zap className="w-3.5 h-3.5" />
              Open Disruptions
            </button>
            <button
              className="btn-ghost text-xs px-4 py-2"
              onClick={() => dispatch({ type: 'SET_VIEW', view: 'graph' })}
            >
              View dependency graph
            </button>
          </div>
        }
      />
    );
  }

  const excluded    = comparisonResult?.excluded ?? [];
  const recommendation = comparisonResult?.recommendation;
  const baseline    = comparisonResult?.baseline;

  function handleSelect(optionId: string) {
    dispatch({ type: 'SELECT_OPTION', optionId });
    dispatch({ type: 'LOG_ACTIVITY', stage: 'option_selected', label: 'Option selected for review', status: 'done', detail: optionId });
    dispatch({ type: 'SET_VIEW', view: 'review' });
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1">
          <Trophy className="w-4 h-4 text-acid" />
          <span className="text-sm font-semibold text-ink">Recovery Options</span>
          {comparisonResult && (
            <span className="text-xs text-ink-muted">
              {comparisonResult.feasible_count} feasible · {comparisonResult.scorable_count} scorable
            </span>
          )}
        </div>
        {ranked.length >= 2 && (
          <button
            className={clsx('btn-ghost text-xs', compareMode && 'border-acid/60 text-acid bg-acid/10')}
            onClick={() => setCompareMode(!compareMode)}
          >
            <BarChart2 className="w-3.5 h-3.5" />
            {compareMode ? 'List view' : 'Compare side-by-side'}
          </button>
        )}
      </div>

      {/* No-recovery outcome */}
      {comparisonResult?.outcome === 'no_feasible_recovery' && (
        <div className="flex items-start gap-3 p-3 bg-fail/10 border border-fail/40 rounded-lg">
          <XCircle className="w-4 h-4 text-fail flex-shrink-0 mt-0.5" />
          <p className="text-xs text-fail">
            No feasible recovery option was found. All options were excluded due to constraint violations.
          </p>
        </div>
      )}

      {/* Recommended banner */}
      {recommendation && (
        <div className="flex items-start gap-3 p-3 bg-ok/10 border border-ok/30 rounded-lg">
          <Sparkles className="w-4 h-4 text-ok flex-shrink-0 mt-0.5" />
          <div className="text-xs space-y-0.5">
            <p className="text-ok font-semibold">Backend recommendation</p>
            <OptionLabel optionId={recommendation} />
          </div>
        </div>
      )}

      {/* Baseline info */}
      {baseline && (
        <div className="flex items-center gap-2 text-xs text-ink-muted px-1 flex-wrap">
          <span>Baseline critical path:</span>
          <span className="font-mono text-ink-secondary">{baseline.path.join(' → ')}</span>
          <span className="ml-auto text-ink-muted">length: <span className="text-ink-secondary font-mono">{baseline.length} {baseline.unit}</span></span>
        </div>
      )}

      {/* Simulated / read-only notice */}
      <p className="text-[11px] text-ink-muted uppercase tracking-widest px-1">
        Simulated comparison · read-only backend — nothing is approved or committed here
      </p>

      {/* Compare mode */}
      {compareMode && ranked.length >= 2 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {ranked.slice(0, 4).map((entry) => (
            <CompareCard
              key={entry.option_id}
              entry={entry}
              isRecommended={entry.option_id === recommendation}
              isSelected={entry.option_id === selectedOptionId}
              baseline={baseline}
              onSelect={() => handleSelect(entry.option_id)}
            />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {ranked.map((entry) => (
            <OptionCard
              key={entry.option_id}
              entry={entry}
              recoveryOption={recoveryResult?.options.find((o) => o.option_id === entry.option_id) ?? null}
              isRecommended={entry.option_id === recommendation}
              isSelected={entry.option_id === selectedOptionId}
              expanded={expandedId === entry.option_id}
              onToggle={() => handleToggle(entry.option_id)}
              onSelect={() => handleSelect(entry.option_id)}
              baseline={baseline}
            />
          ))}

          {/* Excluded section */}
          {excluded.length > 0 && (
            <div className="pt-2">
              <p className="text-[11px] uppercase tracking-wider text-ink-muted mb-2 px-1">
                Excluded ({excluded.length} infeasible)
              </p>
              {excluded.map((entry) => (
                <ExcludedCard key={entry.option_id} entry={entry} />
              ))}
            </div>
          )}

          {ranked.length === 0 && excluded.length === 0 && (
            <EmptyState
              icon={Info}
              title="No options returned"
              description="The backend returned an empty option list for this scenario."
              action={
                <button
                  className="btn-ghost text-xs px-4 py-2"
                  onClick={() => dispatch({ type: 'SET_VIEW', view: 'disruptions' })}
                >
                  <Zap className="w-3.5 h-3.5" />
                  Open Disruptions
                </button>
              }
            />
          )}
        </div>
      )}

      {/* Weights footnote */}
      {comparisonResult && <WeightsFootnote weights={comparisonResult.weights} />}
    </div>
  );
}

// ── Option card (list mode) ───────────────────────────────────────────────────

interface CardProps {
  entry: RankedEntry;
  recoveryOption: RecoveryOption | null;
  isRecommended: boolean;
  isSelected: boolean;
  expanded: boolean;
  onToggle: () => void;
  onSelect: () => void;
  baseline?: { length: number; unit: string } | null;
}

function OptionCard({ entry, recoveryOption, isRecommended, isSelected, expanded, onToggle, onSelect, baseline }: CardProps) {
  const delta = entry.forecast?.delay_delta;
  const forecastLen = entry.forecast?.forecast_length;
  const parsed = parseOptionId(entry.option_id);

  // Unblocks list from the recovery result (if available)
  const unblockMatch = entry.option_id.match(/^drop-dependency:(.+)->(.+)$/);

  return (
    <div className={clsx(
      'card border transition-colors',
      isSelected ? 'border-acid/60 bg-acid/[0.04]'
        : isRecommended ? 'border-ok/40 bg-ok/5'
        : 'border-border',
    )}>
      {/* Summary row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-700/30 transition-colors"
        onClick={onToggle}
      >
        {/* Rank */}
        <span className={clsx(
          'w-7 h-7 flex items-center justify-center rounded-full text-xs font-bold flex-shrink-0',
          entry.rank === 1 ? 'bg-acid/15 text-acid ring-1 ring-acid/50' : 'bg-surface-700 text-ink-secondary',
        )}>
          {entry.rank != null ? `#${entry.rank}` : '—'}
        </span>

        {/* Label */}
        <div className="flex-1 min-w-0 space-y-0.5">
          {parsed ? (
            <p className="text-xs text-ink-secondary">
              Remove: <span className="font-mono text-info">{parsed.dependent}</span>
              {' → '}<span className="font-mono text-fail">{parsed.prerequisite}</span>
            </p>
          ) : (
            <p className="text-xs font-mono text-ink-muted truncate">{entry.option_id}</p>
          )}
          {isSelected && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-acid">
              <ArrowRight className="w-2.5 h-2.5" /> Selected for review
            </span>
          )}
          {isRecommended && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-ok">
              <Sparkles className="w-2.5 h-2.5" /> Recommended
            </span>
          )}
        </div>

        {/* Score */}
        {entry.scorable && entry.score != null && (
          <div className="text-right flex-shrink-0 hidden sm:block">
            <p className="text-[11px] text-ink-muted uppercase">Score</p>
            <p className="text-xs font-mono text-ink-secondary">{entry.score.toFixed(2)}</p>
          </div>
        )}

        {/* Delay badge */}
        {delta != null && <DelayBadge delta={delta} unit={entry.forecast?.unit ?? ''} />}

        <span className="text-ink-muted flex-shrink-0">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-border px-4 py-4 space-y-4">
          {/* Backend-provided description (find_alternative_dependencies) */}
          {recoveryOption && (
            <div className="space-y-2">
              <p className="text-[11px] uppercase tracking-wide text-ink-muted">Backend description</p>
              <p className="text-xs text-ink-secondary leading-relaxed">{recoveryOption.description}</p>
              {recoveryOption.unblocks.length > 0 && (
                <div className="space-y-1">
                  <p className="text-[11px] uppercase tracking-wide text-ink-muted">Unblocks (backend)</p>
                  <div className="flex flex-wrap gap-1">
                    {recoveryOption.unblocks.map((id) => (
                      <span key={id} className="badge bg-ok/10 text-ok border border-ok/40 font-mono">{id}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Change summary */}
          {unblockMatch && (
            <ChangeSummary dependent={unblockMatch[1]} prerequisite={unblockMatch[2]} />
          )}

          {/* Why this option ranks where it does (backend reasons) */}
          {entry.reasons.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] uppercase tracking-wide text-ink-muted">Backend reasons</p>
              {entry.reasons.map((r, i) => (
                <p key={i} className="text-xs text-ink-secondary leading-relaxed">• {r}</p>
              ))}
            </div>
          )}

          {/* Unscored options must say which metrics were missing */}
          {!entry.scorable && entry.missing_metrics.length > 0 && (
            <p className="text-[11px] text-warn leading-relaxed">
              Not scoreable — missing metrics:{' '}
              <span className="font-mono">{entry.missing_metrics.join(', ')}</span>
            </p>
          )}

          {/* Metrics grid */}
          {entry.forecast && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-ink-muted mb-2">Forecast comparison</p>
              {entry.forecast.feasible === false && (
                <p className="text-[11px] text-fail mb-2 font-mono">
                  {entry.forecast.error
                    ? `[${entry.forecast.error.code}] ${entry.forecast.error.message}`
                    : 'This option\'s forecast is infeasible — no forecast values.'}
                </p>
              )}
              <div className="grid grid-cols-3 gap-3 text-xs">
                <MetricCell label="Baseline" value={baseline ? `${baseline.length}` : '—'} unit={baseline?.unit ?? ''} />
                <MetricCell
                  label="Forecast"
                  value={forecastLen != null ? `${forecastLen}` : '—'}
                  unit={entry.forecast.unit}
                  highlight={delta != null && delta > 0 ? 'warn' : delta != null && delta < 0 ? 'good' : undefined}
                />
                <MetricCell
                  label="Δ Delay"
                  value={delta != null ? `${delta >= 0 ? '+' : ''}${delta}` : '—'}
                  unit={entry.forecast.unit}
                  highlight={delta != null && delta > 0 ? 'warn' : delta != null && delta < 0 ? 'good' : undefined}
                  large
                />
              </div>
            </div>
          )}

          {/* Before / after paths */}
          {entry.forecast?.baseline_path && entry.forecast.forecast_path && (
            <PathDiff
              baseline={entry.forecast.baseline_path}
              forecast={entry.forecast.forecast_path}
            />
          )}

          {/* Limitations (first 2) */}
          {entry.limitations.length > 0 && (
            <div className="space-y-1">
              {entry.limitations.slice(0, 2).map((l, i) => (
                <p key={i} className="text-[11px] text-ink-muted italic leading-relaxed">{l}</p>
              ))}
            </div>
          )}

          {/* CTA — review only, no approval flow */}
          <div className="pt-1">
            <button className="btn-primary text-sm px-4 py-2" onClick={onSelect}>
              <ArrowRight className="w-4 h-4" />
              Review proposed plan
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Compare card ──────────────────────────────────────────────────────────────

function CompareCard({ entry, isRecommended, isSelected, baseline, onSelect }: {
  entry: RankedEntry;
  isRecommended: boolean;
  isSelected: boolean;
  baseline?: { length: number; unit: string } | null;
  onSelect: () => void;
}) {
  const delta = entry.forecast?.delay_delta;
  const parsed = parseOptionId(entry.option_id);
  return (
    <div className={clsx(
      'card p-4 flex flex-col gap-3',
      isSelected && 'border-acid/60 bg-acid/[0.04]',
      !isSelected && isRecommended && 'border-ok/40 bg-ok/5',
    )}>
      <div className="flex items-center gap-2">
        {entry.rank != null && (
          <span className="text-xs font-bold text-acid">#{entry.rank}</span>
        )}
        {isSelected && <span className="badge bg-acid/10 text-acid border border-acid/50 text-[11px]">SELECTED</span>}
        {isRecommended && <span className="tag-healthy text-[11px]"><Sparkles className="w-2.5 h-2.5 inline mr-0.5" />REC</span>}
        {delta != null && <DelayBadge delta={delta} unit={entry.forecast?.unit ?? ''} />}
      </div>

      <div className="text-xs text-ink-secondary space-y-0.5">
        {parsed ? (
          <>
            <p>Remove dep:</p>
            <p className="font-mono text-info text-[11px] break-all">{parsed.dependent}</p>
            <p className="text-ink-muted">→ no longer requires</p>
            <p className="font-mono text-fail text-[11px] break-all">{parsed.prerequisite}</p>
          </>
        ) : (
          <p className="font-mono break-all">{entry.option_id}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <MetricCell label="Score" value={entry.score?.toFixed(2) ?? '—'} unit="" />
        <MetricCell label="Baseline" value={`${baseline?.length ?? '—'}`} unit={baseline?.unit ?? ''} />
        <MetricCell label="Forecast" value={`${entry.forecast?.forecast_length ?? '—'}`} unit={entry.forecast?.unit ?? ''} />
        <MetricCell
          label="Δ"
          value={delta != null ? `${delta >= 0 ? '+' : ''}${delta}` : '—'}
          unit={entry.forecast?.unit ?? ''}
          highlight={delta != null && delta > 0 ? 'warn' : 'good'}
        />
      </div>

      <button className="btn-primary text-xs mt-auto justify-center" onClick={onSelect}>
        Review proposed plan
      </button>
    </div>
  );
}

// ── Excluded card ─────────────────────────────────────────────────────────────

function ExcludedCard({ entry }: { entry: RankedEntry }) {
  const [exp, setExp] = useState(false);
  const parsed = parseOptionId(entry.option_id);
  return (
    <div className="card border border-border opacity-60 mb-1">
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-700/30 transition-colors"
        onClick={() => setExp((v) => !v)}
      >
        <XCircle className="w-3.5 h-3.5 text-fail flex-shrink-0" />
        <span className="text-xs text-ink-muted flex-1 min-w-0">
          {parsed
            ? `Remove: ${parsed.dependent} → ${parsed.prerequisite}`
            : <span className="font-mono truncate">{entry.option_id}</span>
          }
        </span>
        <span className="text-[11px] text-fail flex-shrink-0">infeasible</span>
        {exp ? <ChevronUp className="w-3.5 h-3.5 text-ink-muted" /> : <ChevronDown className="w-3.5 h-3.5 text-ink-muted" />}
      </button>
      {exp && entry.violations.length > 0 && (
        <div className="px-4 pb-3 space-y-1 border-t border-border">
          {entry.violations.map((v, i) => (
            <p key={i} className="text-[11px] text-fail font-mono">[{v.code}] {v.message}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Change summary ────────────────────────────────────────────────────────────

function ChangeSummary({ dependent, prerequisite }: { dependent: string; prerequisite: string }) {
  return (
    <div className="bg-surface-800 border border-border rounded-lg p-3 space-y-2">
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">Change summary</p>
      <div className="flex items-center gap-2 text-xs flex-wrap">
        <span className="tag-blocked text-[11px]">Remove edge</span>
        <span className="font-mono text-info">{dependent}</span>
        <ArrowRight className="w-3.5 h-3.5 text-ink-muted flex-shrink-0" />
        <span className="font-mono text-fail">{prerequisite}</span>
      </div>
      <p className="text-[11px] text-ink-muted leading-relaxed">
        <strong className="text-ink-secondary">{dependent}</strong> will no longer require{' '}
        <strong className="text-ink-secondary">{prerequisite}</strong> to complete before it can proceed.
        Downstream work blocked by this path may be unblocked.
      </p>
      <p className="text-[11px] text-warn italic">
        Structural only — business readiness is not assessed.
      </p>
    </div>
  );
}

// ── Path diff ─────────────────────────────────────────────────────────────────

function PathDiff({ baseline, forecast }: { baseline: string[]; forecast: string[] }) {
  const baseSet = new Set(baseline);
  const foreSet = new Set(forecast);
  const removed = baseline.filter((id) => !foreSet.has(id));
  const added   = forecast.filter((id) => !baseSet.has(id));

  return (
    <div className="space-y-2">
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">Critical-path changes</p>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <p className="text-[11px] text-ink-muted">Baseline path</p>
          <p className="text-[11px] font-mono text-ink-secondary leading-relaxed">{baseline.join(' → ')}</p>
        </div>
        <div className="space-y-1">
          <p className="text-[11px] text-ink-muted">Forecast path</p>
          <p className="text-[11px] font-mono text-ink-secondary leading-relaxed">{forecast.join(' → ')}</p>
        </div>
      </div>
      {(removed.length > 0 || added.length > 0) && (
        <div className="flex gap-4 text-[11px] flex-wrap">
          {removed.length > 0 && (
            <span className="text-fail">
              − removed: {removed.map((id) => <span key={id} className="font-mono">{id}</span>).reduce((a, b) => <>{a}, {b}</>)}
            </span>
          )}
          {added.length > 0 && (
            <span className="text-ok">
              + added: {added.map((id) => <span key={id} className="font-mono">{id}</span>).reduce((a, b) => <>{a}, {b}</>)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function MetricCell({ label, value, unit, highlight, large }: {
  label: string; value: string; unit: string;
  highlight?: 'warn' | 'good'; large?: boolean;
}) {
  return (
    <div className="bg-surface-800 rounded p-2">
      <p className="text-[11px] text-ink-muted uppercase tracking-wide">{label}</p>
      <p className={clsx(
        'font-mono font-bold',
        large ? 'text-xl' : 'text-sm',
        highlight === 'warn' ? 'text-warn' : highlight === 'good' ? 'text-ok' : 'text-ink',
      )}>
        {value}
      </p>
      {unit && <p className="text-[11px] text-ink-muted">{unit}</p>}
    </div>
  );
}

function DelayBadge({ delta, unit }: { delta: number; unit: string }) {
  if (delta === 0) return <span className="badge bg-surface-700 text-ink-muted border border-border">Δ 0</span>;
  return (
    <span className={clsx(
      'badge font-mono',
      delta > 0 ? 'bg-warn/15 text-warn border border-warn/40' : 'bg-ok/15 text-ok border border-ok/40',
    )}>
      {delta > 0 ? '+' : ''}{delta} {unit}
    </span>
  );
}

function WeightsFootnote({ weights }: { weights: ComparisonResult['weights'] }) {
  const active = Object.entries(weights).filter(([, v]) => v !== 0);
  return (
    <p className="text-[11px] text-ink-muted border-t border-border pt-2 px-1">
      Ranking by backend score. Active weights: {active.map(([k, v]) => `${k}=${v}`).join(', ')}.
      Lower score = better. Scores compare options within this call only.
    </p>
  );
}
