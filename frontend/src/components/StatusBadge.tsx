const toneMap: Record<string, string> = {
  delivered: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  succeeded: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  online: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  active: "bg-blue-50 text-blue-700 ring-blue-600/20",
  failed: "bg-red-50 text-red-700 ring-red-600/20",
  dead_letter: "bg-red-50 text-red-700 ring-red-600/20",
  offline: "bg-gray-50 text-gray-600 ring-gray-500/20",
  aborted: "bg-red-50 text-red-700 ring-red-600/20",
  cancelled: "bg-gray-100 text-gray-600 ring-gray-500/20",
  pending: "bg-amber-50 text-amber-700 ring-amber-600/20",
  planned: "bg-blue-50 text-blue-700 ring-blue-600/20",
  assigned: "bg-blue-50 text-blue-700 ring-blue-600/20",
  arrived: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  in_transit: "bg-violet-50 text-violet-700 ring-violet-600/20",
  picked_up: "bg-sky-50 text-sky-700 ring-sky-600/20",
  on_break: "bg-amber-50 text-amber-700 ring-amber-600/20",
  rescheduled: "bg-orange-50 text-orange-700 ring-orange-600/20",
  expired: "bg-amber-100 text-amber-800 ring-amber-700/20",
};

const fallback = "bg-gray-50 text-gray-600 ring-gray-500/20";

export function StatusBadge({ value }: { value: string }) {
  const tone = value.toLowerCase();
  const classes = toneMap[tone] ?? fallback;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${classes}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}
