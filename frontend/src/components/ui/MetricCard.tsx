import { clsx } from 'clsx';

interface Props {
  label: string;
  /** The metric value. Pass '—' (or null) when data is unavailable — never invent numbers. */
  value: React.ReactNode;
  unit?: string;
  /** Semantic accent for the value: success / warning / failure / info / neutral. */
  tone?: 'ok' | 'warn' | 'fail' | 'info' | 'neutral';
  hint?: string;
  className?: string;
}

const TONE_CLS: Record<NonNullable<Props['tone']>, string> = {
  ok:      'text-ok',
  warn:    'text-warn',
  fail:    'text-fail',
  info:    'text-info',
  neutral: 'text-ink',
};

/**
 * MetricCard — oversized numeric readout with a compact technical label.
 * Used on the overview page for real backend-derived metrics.
 */
export function MetricCard({ label, value, unit, tone = 'neutral', hint, className }: Props) {
  return (
    <div
      className={clsx(
        'card px-4 py-3.5 flex flex-col justify-between gap-1 min-w-0',
        className,
      )}
      title={hint}
    >
      <span className="label-tech truncate">{label}</span>
      <div className="flex items-baseline gap-1.5 min-w-0">
        <span className={clsx('text-2xl sm:text-3xl font-bold font-display leading-none truncate', TONE_CLS[tone])}>
          {value}
        </span>
        {unit && <span className="text-[10px] text-ink-muted font-mono truncate">{unit}</span>}
      </div>
    </div>
  );
}
