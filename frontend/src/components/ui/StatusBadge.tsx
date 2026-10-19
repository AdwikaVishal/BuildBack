import { clsx } from 'clsx';
import type { TriageState, EntityStatus } from '../../types';

type AnyStatus = TriageState | EntityStatus | 'simulated' | string;

const CLASS_MAP: Record<string, string> = {
  blocked:     'tag-blocked',
  external:    'tag-external',
  at_risk:     'tag-at_risk',
  healthy:     'tag-healthy',
  done:        'tag-done',
  pending:     'tag-pending',
  in_progress: 'tag-in_progress',
  simulated:   'tag-simulated',
};

const LABEL_MAP: Record<string, string> = {
  at_risk:     'AT RISK',
  in_progress: 'IN PROGRESS',
  done:        'DONE',
  blocked:     'BLOCKED',
  external:    'EXTERNAL',
  healthy:     'HEALTHY',
  pending:     'PENDING',
  simulated:   'SIMULATED',
};

const DOT_COLORS: Record<string, string> = {
  blocked:     'bg-fail',
  external:    'bg-info',
  at_risk:     'bg-warn',
  healthy:     'bg-ok',
  done:        'bg-ink-muted',
  pending:     'bg-ink-secondary',
  in_progress: 'bg-info',
  simulated:   'bg-warn',
};

interface Props {
  status: AnyStatus;
  showDot?: boolean;
  className?: string;
}

export function StatusBadge({ status, showDot = true, className }: Props) {
  const cls = CLASS_MAP[status] ?? 'badge bg-surface-800 text-ink-secondary border border-surface-700';
  const label = LABEL_MAP[status] ?? status.toUpperCase().replace('_', ' ');
  const dotCls = DOT_COLORS[status] ?? 'bg-ink-muted';

  return (
    <span className={clsx(cls, className)}>
      {showDot && <span className={clsx('w-1.5 h-1.5 rounded-full inline-block', dotCls)} />}
      {label}
    </span>
  );
}
