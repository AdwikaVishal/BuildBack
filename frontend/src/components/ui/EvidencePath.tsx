import { ChevronRight } from 'lucide-react';

interface Props {
  path: string[];
  nameMap?: Record<string, string>;
}

export function EvidencePath({ path, nameMap = {} }: Props) {
  if (path.length === 0) return <span className="text-gray-500 text-xs italic">—</span>;

  return (
    <div className="flex items-center flex-wrap gap-0.5 text-xs font-mono">
      {path.map((id, i) => (
        <span key={id} className="flex items-center gap-0.5">
          <span className="bg-surface-600 border border-surface-500 rounded px-1.5 py-0.5 text-gray-300">
            {nameMap[id] ?? id}
          </span>
          {i < path.length - 1 && (
            <ChevronRight className="w-3 h-3 text-gray-600 flex-shrink-0" />
          )}
        </span>
      ))}
    </div>
  );
}
