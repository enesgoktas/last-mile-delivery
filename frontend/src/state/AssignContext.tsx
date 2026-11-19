import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useAuth } from "./AuthContext";
import {
  getOptimizationTaskStatus,
  triggerAssignOptimal,
  triggerAssignRange,
} from "../api/client";

export type AssignPhase = "idle" | "submitting" | "polling" | "done" | "error";
export type AssignKind = "day" | "range";

export interface AssignResult {
  routes_created: number;
  assigned_count?: number;
  unassigned_count: number;
  solver_status: string;
  span_days?: number;
}

export interface ChunkProgress {
  chunks_completed: number;
  chunks_total: number;
}

interface AssignContextValue {
  assign: (targetDate: string) => Promise<boolean>;
  assignRange: (dateFrom: string, dateTo: string) => Promise<boolean>;
  previewRange: (dateFrom: string, dateTo: string) => Promise<boolean>;
  busy: boolean;
  phase: AssignPhase;
  kind: AssignKind | null;
  targetDate: string | null;
  targetRange: { from: string; to: string } | null;
  result: AssignResult | null;
  progress: ChunkProgress | null;
  error: string | null;
  draft: boolean;
  reset: () => void;
}

const AssignContext = createContext<AssignContextValue | null>(null);

const POLL_INTERVAL_MS = 3_000;

export function AssignProvider({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  const [phase, setPhase] = useState<AssignPhase>("idle");
  const [kind, setKind] = useState<AssignKind | null>(null);
  const [targetDate, setTargetDate] = useState<string | null>(null);
  const [targetRange, setTargetRange] = useState<{ from: string; to: string } | null>(null);
  const [result, setResult] = useState<AssignResult | null>(null);
  const [progress, setProgress] = useState<ChunkProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<boolean>(false);
  const intervalRef = useRef<number | undefined>(undefined);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== undefined) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = undefined;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const pollTask = useCallback(
    (taskId: string) => {
      intervalRef.current = window.setInterval(async () => {
        try {
          const status = await getOptimizationTaskStatus(token!, taskId);
          if (status.status === "SUCCESS") {
            stopPolling();
            const raw = status.result ?? {};
            setResult({
              routes_created: (raw.routes_created as number) ?? 0,
              assigned_count: raw.assigned_count as number | undefined,
              unassigned_count: (raw.unassigned_count as number) ?? 0,
              solver_status: (raw.solver_status as string) ?? "UNKNOWN",
              span_days: raw.span_days as number | undefined,
            });
            setProgress(null);
            setPhase("done");
          } else if (status.status === "FAILURE") {
            stopPolling();
            const raw = status.result ?? {};
            setError((raw.error as string) ?? "Optimization failed");
            setProgress(null);
            setPhase("error");
          } else if (status.status === "PROGRESS") {
            // Chunked range runs emit per-chunk progress via Celery's
            // update_state. Surface it so the UI can render "chunk X of N"
            // instead of an opaque "polling…" spinner.
            const raw = status.result ?? {};
            const completed = raw.chunks_completed as number | undefined;
            const total = raw.chunks_total as number | undefined;
            if (typeof completed === "number" && typeof total === "number") {
              setProgress({ chunks_completed: completed, chunks_total: total });
            }
          }
        } catch {
          // transient network error — retry next tick
        }
      }, POLL_INTERVAL_MS);
    },
    [token, stopPolling],
  );

  const assign = useCallback(
    async (date: string) => {
      if (!token) return false;
      stopPolling();
      setPhase("submitting");
      setKind("day");
      setTargetDate(date);
      setTargetRange(null);
      setError(null);
      setResult(null);

      try {
        const { task_id } = await triggerAssignOptimal(token, date);
        setPhase("polling");
        pollTask(task_id);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to start optimization");
        setPhase("error");
        return false;
      }
    },
    [token, stopPolling, pollTask],
  );

  const _runRange = useCallback(
    async (dateFrom: string, dateTo: string, isDraft: boolean) => {
      if (!token) return false;
      stopPolling();
      setPhase("submitting");
      setKind("range");
      setTargetDate(null);
      setTargetRange({ from: dateFrom, to: dateTo });
      setError(null);
      setResult(null);
      setProgress(null);
      setDraft(isDraft);

      try {
        const { task_id } = await triggerAssignRange(token, dateFrom, dateTo, {
          draft: isDraft,
        });
        setPhase("polling");
        pollTask(task_id);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to start optimization");
        setPhase("error");
        return false;
      }
    },
    [token, stopPolling, pollTask],
  );

  const assignRange = useCallback(
    (dateFrom: string, dateTo: string) => _runRange(dateFrom, dateTo, false),
    [_runRange],
  );

  const previewRange = useCallback(
    (dateFrom: string, dateTo: string) => _runRange(dateFrom, dateTo, true),
    [_runRange],
  );

  const reset = useCallback(() => {
    stopPolling();
    setPhase("idle");
    setKind(null);
    setTargetDate(null);
    setTargetRange(null);
    setResult(null);
    setProgress(null);
    setError(null);
    setDraft(false);
  }, [stopPolling]);

  const busy = phase === "submitting" || phase === "polling";

  return (
    <AssignContext.Provider
      value={{
        assign,
        assignRange,
        previewRange,
        busy,
        phase,
        kind,
        targetDate,
        targetRange,
        result,
        progress,
        error,
        draft,
        reset,
      }}
    >
      {children}
    </AssignContext.Provider>
  );
}

export function useAssign(): AssignContextValue {
  const ctx = useContext(AssignContext);
  if (!ctx) throw new Error("useAssign must be used inside <AssignProvider>");
  return ctx;
}
