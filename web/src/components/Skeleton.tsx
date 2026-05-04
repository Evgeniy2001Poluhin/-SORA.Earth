export const SkeletonCard = ({ lines = 3 }: { lines?: number }) => (
  <div className="animate-pulse bg-gray-800/50 rounded-lg p-6 space-y-3">
    {Array.from({ length: lines }).map((_, i) => (
      <div key={i} className="h-4 bg-gray-700 rounded" style={{ width: `${60 + ((i * 17) % 40)}%` }} />
    ))}
  </div>
);
export const SkeletonTable = ({ rows = 5 }: { rows?: number }) => (
  <div className="space-y-2">
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="h-10 bg-gray-800/50 rounded animate-pulse" />
    ))}
  </div>
);
