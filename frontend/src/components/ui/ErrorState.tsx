import { AlertTriangle, RefreshCw } from 'lucide-react';

interface Props {
  title?: string;
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  /** Extra guidance shown below the error, e.g. how to start the backend. */
  hint?: React.ReactNode;
}

/**
 * ErrorState — an honest failure display. Never hides the underlying error,
 * and offers a recovery action where one exists.
 */
export function ErrorState({
  title = 'Request failed',
  message,
  onRetry,
  retryLabel = 'Retry',
  hint,
}: Props) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-card border border-fail/40 bg-fail/5 p-4"
    >
      <AlertTriangle className="w-4 h-4 text-fail flex-shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="text-sm font-semibold text-fail">{title}</p>
        <p className="text-xs text-ink-secondary font-mono break-words leading-relaxed">{message}</p>
        {hint && <div className="text-xs text-ink-muted pt-1">{hint}</div>}
        {onRetry && (
          <button className="btn-ghost text-xs mt-2" onClick={onRetry}>
            <RefreshCw className="w-3.5 h-3.5" />
            {retryLabel}
          </button>
        )}
      </div>
    </div>
  );
}
