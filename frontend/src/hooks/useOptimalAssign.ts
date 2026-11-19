import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../state/AuthContext";
import { getOptimizationTaskStatus, triggerAssignOptimal } from "../api/client";

type AssignState = "idle" | "submitting" | "polling" | "done" | "error";

export interface AssignResult {
  routes_created: number;
  unassigned_count: number;
  solver_status: string;
}

export interface UseOptimalAssignReturn {
  assign: (targetDate: string) => Promise<void>;
  busy: boolean;
  state: AssignState;
  result: AssignResult | null;
  error: string | null;
  reset: () => void;
}

const POLL_INTERVAL_MS = 3_000;

export function useOptimalAssign(): UseOptimalAssignReturn {
  const { token } = useAuth();
  const [state, setState] = useState<AssignState>("idle");
  const [result, setResult] = useState<AssignResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<number | undefined>(undefined);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== undefined) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = undefined;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const assign = useCallback(
    async (targetDate: string) => {
      if (!token) return;
      stopPolling();
      setState("submitting");
      setError(null);
      setResult(null);

      try {
        const { task_id } = await triggerAssignOptimal(token, targetDate);
        setState("polling");

        intervalRef.current = window.setInterval(async () => {
          try {
            const status = await getOptimizationTaskStatus(token, task_id);
            if (status.status === "SUCCESS") {
              stopPolling();
              const raw = status.result ?? {};
              setResult({
                routes_created: (raw.routes_created as number) ?? 0,
                unassigned_count: (raw.unassigned_count as number) ?? 0,
                solver_status: (raw.solver_status as string) ?? "UNKNOWN",
              });
              setState("done");
            } else if (status.status === "FAILURE") {
              stopPolling();
              const raw = status.result ?? {};
              setError((raw.error as string) ?? "Optimization failed");
              setState("error");
            }
            // PENDING, STARTED, RETRY → keep polling
          } catch {
            // transient fetch error — keep polling, will retry next interval
          }
        }, POLL_INTERVAL_MS);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to start optimization");
        setState("error");
      }
    },
    [token, stopPolling],
  );

  const reset = useCallback(() => {
    stopPolling();
    setState("idle");
    setResult(null);
    setError(null);
  }, [stopPolling]);

  const busy = state === "submitting" || state === "polling";
  return { assign, busy, state, result, error, reset };
}
