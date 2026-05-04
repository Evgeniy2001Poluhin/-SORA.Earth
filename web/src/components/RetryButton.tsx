interface Props { error: Error; onRetry: () => void; label?: string }
export const RetryButton = ({ error, onRetry, label = 'Retry' }: Props) => (
  <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-center">
    <p className="text-sm text-red-300 mb-3">⚠ {error.message || 'Failed to load'}</p>
    <button onClick={onRetry} className="px-4 py-2 bg-red-500 hover:bg-red-400 text-white rounded">
      {label}
    </button>
  </div>
);
