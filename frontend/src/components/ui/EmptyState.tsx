import type { LucideIcon } from 'lucide-react';
import { clsx } from 'clsx';

interface Props {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: Props) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center gap-3 py-14 text-center rounded-card border border-dashed border-surface-700 bg-surface-900/40 px-6',
        className,
      )}
    >
      {Icon && (
        <span className="w-12 h-12 rounded-full border border-surface-700 bg-surface-850 flex items-center justify-center">
          <Icon className="w-5 h-5 text-ink-muted" strokeWidth={1.5} />
        </span>
      )}
      <div>
        <p className="text-sm font-semibold text-ink-secondary font-display">{title}</p>
        {description && <p className="text-xs text-ink-muted mt-1 max-w-sm mx-auto leading-relaxed">{description}</p>}
      </div>
      {action}
    </div>
  );
}
