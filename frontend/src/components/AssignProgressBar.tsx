import { useEffect, useRef, useState } from "react";
import { CheckCircle2, XCircle, Zap } from "lucide-react";
import { useAssign } from "../state/AssignContext";

/**
 * Fixed bottom-right card that shows while an "Assign Optimally" task is
 * running. It fakes smooth progress during the indeterminate polling phase
 * and auto-dismisses a few seconds after the task completes.
 */
export function AssignProgressBar() {
  const { phase, kind, targetDate, targetRange, result, error, reset } = useAssign();

  // Fake progress: ramps to ~85% while polling, jumps to 100% on done.
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | undefined>(undefined);
  const startRef = useRef<number | undefined>(undefined);

  // How long (ms) we expect the solver to take — used to pace the bar.
  const EXPECTED_MS = 65_000;

  useEffect(() => {
    if (phase === "submitting") {
      setProgress(0);
      startRef.current = performance.now();
    }

    if (phase === "polling") {
      if (startRef.current === undefined) startRef.current = performance.now();

      const tick = (now: number) => {
        const elapsed = now - (startRef.current ?? now);
        // Ease-out curve: approaches 85% asymptotically
        const raw = 1 - Math.exp(-elapsed / EXPECTED_MS);
        setProgress(Math.min(raw * 85, 85));
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      return () => {
        if (rafRef.current !== undefined) cancelAnimationFrame(rafRef.current);
      };
    }

    if (phase === "done" || phase === "error") {
      if (rafRef.current !== undefined) cancelAnimationFrame(rafRef.current);
      setProgress(100);

      const timeout = window.setTimeout(() => reset(), phase === "done" ? 4_000 : 6_000);
      return () => clearTimeout(timeout);
    }

    if (phase === "idle") {
      setProgress(0);
      startRef.current = undefined;
    }
  }, [phase, reset]);

  if (phase === "idle") return null;

  const dateLabel = targetDate
    ? new Date(targetDate + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : "";
  const rangeLabel = targetRange
    ? `${new Date(targetRange.from + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })}–${new Date(targetRange.to + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })}`
    : "";
  const actionLabel = kind === "range" ? "Range optimization" : "Assignment";

  const isActive = phase === "submitting" || phase === "polling";
  const isDone = phase === "done";
  const isError = phase === "error";

  return (
    <div
      className={[
        "fixed bottom-5 right-5 z-50 w-72 rounded-xl border shadow-xl overflow-hidden",
        "transition-all duration-300",
        isDone
          ? "bg-white border-emerald-200"
          : isError
            ? "bg-white border-red-200"
            : "bg-white border-slate-200",
      ].join(" ")}
    >
      {/* Content */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <span
          className={[
            "mt-0.5 shrink-0 w-7 h-7 rounded-full flex items-center justify-center",
            isDone
              ? "bg-emerald-50 text-emerald-600"
              : isError
                ? "bg-red-50 text-red-500"
                : "bg-orange-50 text-accent-500",
          ].join(" ")}
        >
          {isDone ? (
            <CheckCircle2 size={15} strokeWidth={2} />
          ) : isError ? (
            <XCircle size={15} strokeWidth={2} />
          ) : (
            <Zap size={14} strokeWidth={2} className={isActive ? "animate-pulse" : ""} />
          )}
        </span>

        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-slate-800 leading-tight">
            {isDone
              ? `${actionLabel} complete`
              : isError
                ? `${actionLabel} failed`
                : phase === "submitting"
                  ? "Starting optimizer…"
                  : kind === "range"
                    ? `Optimizing range · ${rangeLabel}`
                    : `Assigning routes · ${dateLabel}`}
          </p>
          <p className="text-[11px] text-slate-400 mt-0.5 leading-tight">
            {isDone && result
              ? kind === "range"
                ? `${result.routes_created} route${result.routes_created !== 1 ? "s" : ""} created · ${
                    result.assigned_count ?? 0
                  } assigned · ${result.unassigned_count} unassigned`
                : `${result.routes_created} route${result.routes_created !== 1 ? "s" : ""} created · ${result.unassigned_count} unassigned`
              : isError
                ? (error ?? "Unknown error — please try again")
                : phase === "submitting"
                  ? "Sending request to the server…"
                  : "OR-Tools solving — this takes ~60 s"}
          </p>
        </div>
      </div>

      {/* Progress track */}
      <div className="h-1 w-full bg-slate-100">
        <div
          className={[
            "h-full transition-all",
            isDone
              ? "bg-emerald-500 duration-500"
              : isError
                ? "bg-red-400 duration-300"
                : "bg-accent-500 duration-300",
          ].join(" ")}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
