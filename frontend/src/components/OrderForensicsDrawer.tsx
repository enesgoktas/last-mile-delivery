import { useEffect, useState } from "react";
import {
  ArrowDown,
  Clock,
  GitBranch,
  MapPin,
  Package,
  Timer,
  Truck,
  X as XIcon,
} from "lucide-react";
import { SlideOver } from "./ui/SlideOver";
import { useAuth } from "../state/AuthContext";
import { getOrderForensics } from "../api/client";
import type {
  OrderForensicsDispatchDecisionRow,
  OrderForensicsETAPredictionRow,
  OrderForensicsResponse,
  OrderForensicsStatusHistoryRow,
  OrderForensicsStopHistoryRow,
  OrderForensicsStopRow,
} from "../types";

interface Props {
  open: boolean;
  orderId: string | null;
  onClose: () => void;
}

// Merge every event source into one chronological timeline so the
// dispatcher reads "what happened" top-to-bottom without jumping
// between sections.
type TimelineEvent =
  | {
      kind: "status";
      at: string;
      data: OrderForensicsStatusHistoryRow;
    }
  | {
      kind: "stop_history";
      at: string;
      data: OrderForensicsStopHistoryRow;
    }
  | {
      kind: "dispatch_decision";
      at: string;
      data: OrderForensicsDispatchDecisionRow;
    }
  | {
      kind: "eta_prediction";
      at: string;
      data: OrderForensicsETAPredictionRow;
    };

function fmtDT(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const abs = Math.abs(seconds);
  if (abs < 60) return `${Math.round(seconds)}s`;
  if (abs < 3600) return `${(seconds / 60).toFixed(0)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function StatusPill({ value }: { value: string }) {
  const map: Record<string, string> = {
    PENDING: "bg-amber-50 text-amber-700 ring-amber-200",
    ASSIGNED: "bg-sky-50 text-sky-700 ring-sky-200",
    PICKED_UP: "bg-sky-50 text-sky-700 ring-sky-200",
    IN_TRANSIT: "bg-blue-50 text-blue-700 ring-blue-200",
    DELIVERED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    FAILED: "bg-red-50 text-red-700 ring-red-200",
    CANCELLED: "bg-slate-50 text-slate-700 ring-slate-200",
    EXPIRED: "bg-slate-50 text-slate-700 ring-slate-200",
    DRAFT: "bg-slate-50 text-slate-600 ring-slate-200",
    RESCHEDULED: "bg-amber-50 text-amber-700 ring-amber-200",
    ARRIVED: "bg-sky-50 text-sky-700 ring-sky-200",
    COMPLETED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  };
  const cls = map[value] ?? "bg-slate-50 text-slate-700 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded-md ring-1 ring-inset ${cls} tabular-nums`}
    >
      {value}
    </span>
  );
}

export function OrderForensicsDrawer({ open, orderId, onClose }: Props) {
  const { token } = useAuth();
  const [data, setData] = useState<OrderForensicsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !orderId || !token) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    getOrderForensics(token, orderId)
      .then((r) => {
        if (cancelled) return;
        setData(r);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load forensics");
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, orderId, token]);

  const subtitle = data
    ? data.order.external_ref ?? data.order.id.slice(0, 8)
    : orderId
    ? orderId.slice(0, 8)
    : "";

  return (
    <SlideOver
      open={open}
      onClose={onClose}
      title="Order forensics"
      subtitle={subtitle}
      width="w-[640px]"
    >
      {loading && (
        <div className="px-6 py-6 space-y-3 animate-pulse" aria-busy="true">
          <div className="h-12 bg-slate-100 rounded" />
          <div className="h-3 bg-slate-100 rounded w-1/2" />
          <div className="h-24 bg-slate-100 rounded" />
          <div className="h-40 bg-slate-100 rounded" />
        </div>
      )}
      {error != null && !loading && (
        <div className="px-6 py-6">
          <p className="text-sm text-red-600">Could not load: {error}</p>
        </div>
      )}
      {data && !loading && <Body data={data} />}
    </SlideOver>
  );
}

function Body({ data }: { data: OrderForensicsResponse }) {
  const events = buildTimeline(data);
  const phases = groupIntoPhases(events);
  const etaSummary = summarizeETA(data.eta_predictions);
  const totalCycle = computeCycleTime(events);

  return (
    <div className="px-6 py-6 space-y-5">
      {/* Header card — hero status, ref, key window facts, optional
          failure tag. Big enough to anchor the drawer. */}
      <header className="rounded-lg border border-slate-200 bg-white p-5">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Order
            </p>
            <p className="text-lg font-semibold text-slate-900 tabular-nums break-all">
              {data.order.external_ref ?? data.order.id.slice(0, 8)}
            </p>
            {data.order.external_ref && (
              <p className="text-[10px] text-slate-400 font-mono">
                {data.order.id.slice(0, 8)}
              </p>
            )}
          </div>
          <BigStatusPill value={data.order.status} />
        </div>
        {totalCycle != null && (
          <div className="mb-4 rounded-md bg-slate-50 px-3 py-2 flex items-center gap-2 text-xs">
            <Timer size={12} className="text-slate-400" />
            <span className="text-slate-500">Total time in system</span>
            <span className="ml-auto font-semibold tabular-nums text-slate-800">
              {fmtDuration(totalCycle)}
            </span>
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
          <HeaderField
            label="Delivery window"
            value={
              <span className="text-slate-700 tabular-nums">
                {fmtDT(data.order.earliest_delivery)} →{" "}
                {fmtDT(data.order.latest_delivery)}
              </span>
            }
          />
          <HeaderField
            label="Scheduled day"
            value={
              <span className="text-slate-700">
                {data.order.scheduled_for_date ?? "—"}
              </span>
            }
          />
          <HeaderField
            label="Attempts"
            value={
              <span className="text-slate-700 tabular-nums">
                {data.order.attempt_count}
              </span>
            }
          />
          <HeaderField
            label="Priority"
            value={<span className="text-slate-700">{data.order.priority}</span>}
          />
        </div>
        {data.order.failure_cause_category && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            <span className="font-semibold">
              Failed · {data.order.failure_cause_category}
            </span>
            {data.order.failure_cause_subcategory ? (
              <span className="text-red-600">
                {" "}
                · {data.order.failure_cause_subcategory}
              </span>
            ) : null}
          </div>
        )}
      </header>

      {/* ETA accuracy — compact 3-up with strong drift signaling */}
      {etaSummary && <ETAAccuracyBanner summary={etaSummary} />}

      {/* Dispatch decisions */}
      {data.dispatch_decisions.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400 mb-3">
            Dispatch decisions
          </p>
          <ul className="space-y-2">
            {data.dispatch_decisions.map((d, i) => (
              <li key={i} className="text-xs">
                <div className="flex items-center gap-2 text-slate-600">
                  <GitBranch size={11} className="text-indigo-500" />
                  <span className="font-medium text-slate-700">{d.kind}</span>
                  <span className="text-slate-400">·</span>
                  <span className="tabular-nums text-slate-500">
                    {fmtDT(d.created_at)}
                  </span>
                </div>
                <div className="ml-5 mt-0.5 text-slate-600">
                  {d.chosen_courier_id ? (
                    <>
                      Chose{" "}
                      <span className="font-mono">
                        {d.chosen_courier_id.slice(0, 8)}
                      </span>
                      {d.chosen_score != null && (
                        <span className="text-slate-400">
                          {" "}
                          · score {Math.round(d.chosen_score)}
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="text-amber-700">
                      Unassigned ·{" "}
                      {d.decision_reason ?? "no reason recorded"}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Timeline — grouped into operational phases so dispatchers can
          read the lifecycle as a story instead of a flat event list. */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
            Timeline
          </p>
          <span className="text-[10px] text-slate-400 tabular-nums">
            {events.length} {events.length === 1 ? "event" : "events"}
          </span>
        </div>
        {events.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/50 px-4 py-8 text-center">
            <Package size={18} className="mx-auto text-slate-300" />
            <p className="mt-2 text-xs text-slate-500">
              No events recorded yet.
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            {phases.map((phase) => (
              <PhaseSection key={phase.id} phase={phase} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HeaderField({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <div className="mt-0.5">{value}</div>
    </div>
  );
}

function ETAAccuracyBanner({
  summary,
}: {
  summary: {
    predicted_arrival: string;
    actual_arrival: string | null;
    error_s: number | null;
  };
}) {
  const drift = summary.error_s;
  const tone =
    drift == null
      ? "neutral"
      : drift > 300
      ? "late"
      : drift < -300
      ? "early"
      : "on-time";
  const toneClasses: Record<
    "neutral" | "late" | "early" | "on-time",
    { bg: string; border: string; label: string; tag: string }
  > = {
    neutral: {
      bg: "bg-white",
      border: "border-slate-200",
      label: "text-slate-400",
      tag: "—",
    },
    "on-time": {
      bg: "bg-emerald-50",
      border: "border-emerald-200",
      label: "text-emerald-700",
      tag: "On time",
    },
    late: {
      bg: "bg-red-50",
      border: "border-red-200",
      label: "text-red-700",
      tag: "Late",
    },
    early: {
      bg: "bg-sky-50",
      border: "border-sky-200",
      label: "text-sky-700",
      tag: "Early",
    },
  };
  const t = toneClasses[tone];
  return (
    <div className={`rounded-lg border p-4 ${t.bg} ${t.border}`}>
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
          ETA accuracy
        </p>
        <span
          className={`text-[10px] font-semibold uppercase tracking-wide ${t.label}`}
        >
          {t.tag}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <p className="text-[10px] text-slate-500">Predicted</p>
          <p className="text-sm font-semibold text-slate-800 tabular-nums">
            {fmtDT(summary.predicted_arrival)}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-slate-500">Actual</p>
          <p className="text-sm font-semibold text-slate-800 tabular-nums">
            {fmtDT(summary.actual_arrival)}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-slate-500">Drift</p>
          <p
            className={`text-base font-bold tabular-nums ${
              drift == null
                ? "text-slate-400"
                : tone === "late"
                ? "text-red-600"
                : tone === "early"
                ? "text-sky-600"
                : "text-emerald-600"
            }`}
          >
            {drift == null
              ? "—"
              : `${drift >= 0 ? "+" : "−"}${fmtDuration(Math.abs(drift))}`}
          </p>
        </div>
      </div>
    </div>
  );
}

interface Phase {
  id: string;
  label: string;
  tone: "slate" | "sky" | "indigo" | "emerald" | "red";
  events: TimelineEvent[];
}

function groupIntoPhases(events: TimelineEvent[]): Phase[] {
  // Phase boundaries are the load-bearing status transitions:
  //   * Dispatch    — events before ASSIGNED (planning + decisions)
  //   * Pickup      — between ASSIGNED and PICKED_UP
  //   * In transit  — between PICKED_UP and IN_TRANSIT terminal
  //   * Delivery    — DELIVERED/FAILED + their nearby events
  // Events are assigned by their `created_at` ordering against the
  // status milestones; flat lists fall back to a single "Lifecycle"
  // phase.
  const statusEvents = events.filter((e) => e.kind === "status");
  const milestones: Record<string, string> = {};
  for (const e of statusEvents) {
    if (e.kind === "status") {
      const s = e.data.to_status;
      if (!(s in milestones)) milestones[s] = e.at;
    }
  }
  const dispatched = milestones.ASSIGNED;
  const pickedUp = milestones.PICKED_UP;
  const inTransit = milestones.IN_TRANSIT;
  const terminal =
    milestones.DELIVERED ?? milestones.FAILED ?? milestones.CANCELLED;

  const dispatch: TimelineEvent[] = [];
  const pickup: TimelineEvent[] = [];
  const transit: TimelineEvent[] = [];
  const delivery: TimelineEvent[] = [];
  const other: TimelineEvent[] = [];

  for (const e of events) {
    const at = e.at;
    if (dispatched && at < dispatched) {
      dispatch.push(e);
    } else if (pickedUp && at < pickedUp) {
      pickup.push(e);
    } else if (inTransit && at < inTransit) {
      transit.push(e);
    } else if (terminal && at <= terminal) {
      delivery.push(e);
    } else {
      other.push(e);
    }
  }

  const phases: Phase[] = [];
  if (dispatch.length)
    phases.push({
      id: "dispatch",
      label: "Dispatch",
      tone: "indigo",
      events: dispatch,
    });
  if (pickup.length)
    phases.push({ id: "pickup", label: "Pickup", tone: "sky", events: pickup });
  if (transit.length)
    phases.push({
      id: "transit",
      label: "In transit",
      tone: "slate",
      events: transit,
    });
  if (delivery.length)
    phases.push({
      id: "delivery",
      label: milestones.FAILED ? "Failed delivery" : "Delivery",
      tone: milestones.FAILED ? "red" : "emerald",
      events: delivery,
    });
  if (other.length)
    phases.push({ id: "other", label: "Other", tone: "slate", events: other });

  // If there are no status milestones at all, lump everything under
  // a single phase so the timeline still reads.
  if (phases.length === 0 && events.length > 0) {
    phases.push({
      id: "lifecycle",
      label: "Lifecycle",
      tone: "slate",
      events,
    });
  }
  return phases;
}

const PHASE_TONE: Record<
  Phase["tone"],
  { ring: string; bg: string; text: string }
> = {
  slate: { ring: "ring-slate-200", bg: "bg-slate-100", text: "text-slate-600" },
  sky: { ring: "ring-sky-200", bg: "bg-sky-100", text: "text-sky-700" },
  indigo: {
    ring: "ring-indigo-200",
    bg: "bg-indigo-100",
    text: "text-indigo-700",
  },
  emerald: {
    ring: "ring-emerald-200",
    bg: "bg-emerald-100",
    text: "text-emerald-700",
  },
  red: { ring: "ring-red-200", bg: "bg-red-100", text: "text-red-700" },
};

function PhaseSection({ phase }: { phase: Phase }) {
  const tone = PHASE_TONE[phase.tone];
  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${tone.bg} ${tone.text}`}
        >
          {phase.label}
        </span>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {phase.events.length}{" "}
          {phase.events.length === 1 ? "event" : "events"}
        </span>
      </div>
      <ol className="relative border-l border-slate-200 ml-2 pl-5 space-y-3">
        {phase.events.map((e, i) => (
          <li key={i} className="relative">
            <span
              className={`absolute -left-[1.65rem] top-1 flex h-5 w-5 items-center justify-center rounded-full bg-white ring-1 ${tone.ring}`}
            >
              <EventIcon kind={e.kind} />
            </span>
            <EventRow event={e} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function BigStatusPill({ value }: { value: string }) {
  const map: Record<string, string> = {
    PENDING: "bg-amber-50 text-amber-700 ring-amber-200",
    ASSIGNED: "bg-sky-50 text-sky-700 ring-sky-200",
    PICKED_UP: "bg-sky-50 text-sky-700 ring-sky-200",
    IN_TRANSIT: "bg-blue-50 text-blue-700 ring-blue-200",
    DELIVERED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    FAILED: "bg-red-50 text-red-700 ring-red-200",
    CANCELLED: "bg-slate-50 text-slate-700 ring-slate-200",
    EXPIRED: "bg-slate-50 text-slate-700 ring-slate-200",
    DRAFT: "bg-slate-50 text-slate-600 ring-slate-200",
    RESCHEDULED: "bg-amber-50 text-amber-700 ring-amber-200",
  };
  const cls = map[value] ?? "bg-slate-50 text-slate-700 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide rounded-md ring-1 ring-inset ${cls}`}
    >
      {value}
    </span>
  );
}

function computeCycleTime(events: TimelineEvent[]): number | null {
  if (events.length === 0) return null;
  const first = new Date(events[0].at).getTime();
  const last = new Date(events[events.length - 1].at).getTime();
  if (!isFinite(first) || !isFinite(last) || last <= first) return null;
  return Math.round((last - first) / 1000);
}

function EventIcon({ kind }: { kind: TimelineEvent["kind"] }) {
  if (kind === "status") return <Package size={9} className="text-slate-500" />;
  if (kind === "stop_history") return <MapPin size={9} className="text-sky-500" />;
  if (kind === "dispatch_decision")
    return <GitBranch size={9} className="text-indigo-500" />;
  if (kind === "eta_prediction") return <Clock size={9} className="text-amber-500" />;
  return <XIcon size={9} />;
}

function EventRow({ event }: { event: TimelineEvent }) {
  if (event.kind === "status") {
    const d = event.data;
    return (
      <div className="text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-400 tabular-nums">{fmtDT(d.created_at)}</span>
          {d.from_status && <StatusPill value={d.from_status} />}
          {d.from_status && <ArrowDown size={9} className="text-slate-400 -rotate-90" />}
          <StatusPill value={d.to_status} />
        </div>
        {d.reason && (
          <p className="ml-1 mt-0.5 text-slate-600">{d.reason}</p>
        )}
      </div>
    );
  }
  if (event.kind === "stop_history") {
    const d = event.data;
    return (
      <div className="text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-400 tabular-nums">{fmtDT(d.created_at)}</span>
          <span className="text-slate-700 font-medium">Stop</span>
          {d.from_status && <StatusPill value={d.from_status} />}
          {d.from_status && <ArrowDown size={9} className="text-slate-400 -rotate-90" />}
          <StatusPill value={d.to_status} />
        </div>
        {(d.lat != null && d.lon != null) && (
          <p className="ml-1 mt-0.5 text-slate-500 font-mono text-[10px]">
            {d.lat.toFixed(5)}, {d.lon.toFixed(5)}
          </p>
        )}
        {d.reason && (
          <p className="ml-1 mt-0.5 text-slate-600">{d.reason}</p>
        )}
      </div>
    );
  }
  if (event.kind === "dispatch_decision") {
    const d = event.data;
    return (
      <div className="text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-400 tabular-nums">{fmtDT(d.created_at)}</span>
          <span className="text-indigo-700 font-medium">Decision · {d.kind}</span>
        </div>
        <p className="ml-1 mt-0.5 text-slate-600">
          {d.chosen_courier_id ? (
            <>
              Assigned to <span className="font-mono">{d.chosen_courier_id.slice(0, 8)}</span>
              {d.chosen_score != null && (
                <span className="text-slate-400"> · score {Math.round(d.chosen_score)}</span>
              )}
            </>
          ) : (
            <span className="text-amber-700">{d.decision_reason ?? "Unassigned"}</span>
          )}
        </p>
      </div>
    );
  }
  if (event.kind === "eta_prediction") {
    const d = event.data;
    return (
      <div className="text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-400 tabular-nums">{fmtDT(d.predicted_at)}</span>
          <span className="text-amber-700 font-medium">ETA · {d.predicted_by}</span>
        </div>
        <p className="ml-1 mt-0.5 text-slate-600 tabular-nums">
          Predicted arrival: {fmtDT(d.predicted_arrival)}
          {d.actual_arrival && (
            <>
              {" "}
              · actual {fmtDT(d.actual_arrival)}
            </>
          )}
          {d.error_s != null && (
            <span
              className={
                d.error_s > 300
                  ? " text-red-600"
                  : d.error_s < -300
                  ? " text-sky-600"
                  : " text-emerald-600"
              }
            >
              {" "}({d.error_s >= 0 ? "+" : "−"}{fmtDuration(Math.abs(d.error_s))})
            </span>
          )}
        </p>
      </div>
    );
  }
  return null;
}

function buildTimeline(data: OrderForensicsResponse): TimelineEvent[] {
  const events: TimelineEvent[] = [
    ...data.status_history.map(
      (h): TimelineEvent => ({ kind: "status", at: h.created_at, data: h }),
    ),
    ...data.stop_history.map(
      (h): TimelineEvent => ({ kind: "stop_history", at: h.created_at, data: h }),
    ),
    ...data.dispatch_decisions.map(
      (d): TimelineEvent => ({ kind: "dispatch_decision", at: d.created_at, data: d }),
    ),
    ...data.eta_predictions.map(
      (e): TimelineEvent => ({ kind: "eta_prediction", at: e.predicted_at, data: e }),
    ),
  ];
  events.sort((a, b) => a.at.localeCompare(b.at));
  return events;
}

function summarizeETA(rows: OrderForensicsETAPredictionRow[]):
  | { predicted_arrival: string; actual_arrival: string | null; error_s: number | null }
  | null {
  // Use the latest PLANNER row that has a paired actual_arrival (or the
  // latest unpaired one). Falls back to nothing if no rows at all.
  if (rows.length === 0) return null;
  const planner = rows
    .filter((r) => r.predicted_by === "PLANNER")
    .sort((a, b) => b.predicted_at.localeCompare(a.predicted_at))[0];
  const target = planner ?? rows[rows.length - 1];
  return {
    predicted_arrival: target.predicted_arrival,
    actual_arrival: target.actual_arrival,
    error_s: target.error_s,
  };
}

// Re-export the icon list so unused imports don't trip TS strictness.
void Truck;
void Timer;
