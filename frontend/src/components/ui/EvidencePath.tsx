import { ChevronRight } from 'lucide-react';

interface Props {
  path: string[];
  nameMap?: Record<string, string>;
}

export function EvidencePath({ path, nameMap = {} }: Props) {
  if (path.length === 0) return <span className="text-ink-muted text-xs italic">—</span>;

  return (
    <div className="flex items-center flex-wrap gap-0.5 text-xs font-mono">
      {path.map((id, i) => (
        <span key={id} className="flex items-center gap-0.5">
          <span className="bg-surface-800 border border-surface-700 rounded px-1.5 py-0.5 text-ink-secondary">
            {nameMap[id] ?? id}
          </span>
          {i < path.length - 1 && (
            <ChevronRight className="w-3 h-3 text-ink-muted flex-shrink-0" />
          )}
        </span>
      ))}
    </div>
  );
}
