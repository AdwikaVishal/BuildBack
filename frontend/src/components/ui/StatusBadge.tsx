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
  blocked:     'bg-red-400',
  external:    'bg-orange-400',
  at_risk:     'bg-yellow-400',
  healthy:     'bg-emerald-400',
  done:        'bg-gray-500',
  pending:     'bg-purple-400',
  in_progress: 'bg-blue-400',
  simulated:   'bg-amber-400',
};

interface Props {
  status: AnyStatus;
  showDot?: boolean;
  className?: string;
}

export function StatusBadge({ status, showDot = true, className }: Props) {
  const cls = CLASS_MAP[status] ?? 'badge bg-surface-600 text-gray-400 border border-surface-500';
  const label = LABEL_MAP[status] ?? status.toUpperCase().replace('_', ' ');
  const dotCls = DOT_COLORS[status] ?? 'bg-gray-400';

  return (
    <span className={clsx(cls, className)}>
      {showDot && <span className={clsx('w-1.5 h-1.5 rounded-full inline-block', dotCls)} />}
      {label}
    </span>
  );
}
