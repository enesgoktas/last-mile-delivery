interface StatCardProps {
  value: string | number;
  label: string;
  emphasis?: boolean;
  alert?: boolean;
  className?: string;
}

export function StatCard({ value, label, emphasis = false, alert = false, className = "" }: StatCardProps) {
  return (
    <div className={`flex flex-col min-w-0 ${className}`}>
      <span
        className={`tabular-nums leading-none whitespace-nowrap ${
          emphasis
            ? "text-3xl font-bold text-slate-900"
            : alert
            ? "text-2xl font-bold text-red-600"
            : "text-2xl font-bold text-slate-700"
        }`}
      >
        {value}
      </span>
      <span className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </span>
    </div>
  );
}
