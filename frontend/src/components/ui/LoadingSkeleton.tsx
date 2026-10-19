import { clsx } from 'clsx';

/** A single shimmering placeholder block. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx('animate-pulse rounded bg-surface-800', className)}
      aria-hidden="true"
    />
  );
}

/**
 * LoadingSkeleton — labelled loading placeholder for panels and lists.
 * Announced politely to screen readers.
 */
export function LoadingSkeleton({ label = 'Loading…', rows = 4 }: { label?: string; rows?: number }) {
  return (
    <div className="space-y-3 py-4" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <div className="flex items-center gap-2 px-1">
        <div className="w-3 h-3 rounded-full bg-acid/40 animate-pulse" />
        <span className="label-tech">{label}</span>
      </div>
      <div className="space-y-2 px-1">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className={clsx('h-4', i % 2 === 0 ? 'w-3/4' : 'w-1/2')} />
        ))}
      </div>
    </div>
  );
}
