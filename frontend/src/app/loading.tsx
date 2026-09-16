function ShimmerBlock({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-muted ${className ?? ""}`} />;
}

export default function GlobalLoading() {
  return (
    <div role="status" aria-label="Cargando" className="space-y-4 p-6">
      <div className="rounded-xl border bg-card p-6">
        <ShimmerBlock className="mb-3 h-3 w-24" />
        <ShimmerBlock className="mb-4 h-10 w-48" />
        <ShimmerBlock className="h-2 w-full rounded-full" />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-3 rounded-xl border bg-card p-6">
          <ShimmerBlock className="h-3 w-28" />
          <ShimmerBlock className="h-4 w-3/4" />
          <ShimmerBlock className="h-4 w-full" />
        </div>
        <div className="space-y-3 rounded-xl border bg-card p-6">
          <ShimmerBlock className="h-3 w-24" />
          <ShimmerBlock className="h-4 w-full" />
          <ShimmerBlock className="h-4 w-4/5" />
        </div>
      </div>
    </div>
  );
}
