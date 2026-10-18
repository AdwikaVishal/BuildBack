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
    <div className={clsx('flex flex-col items-center justify-center gap-3 py-14 text-center', className)}>
      {Icon && <Icon className="w-10 h-10 text-gray-600" strokeWidth={1} />}
      <div>
        <p className="text-sm font-medium text-gray-400">{title}</p>
        {description && <p className="text-xs text-gray-600 mt-1 max-w-xs mx-auto">{description}</p>}
      </div>
      {action}
    </div>
  );
}
