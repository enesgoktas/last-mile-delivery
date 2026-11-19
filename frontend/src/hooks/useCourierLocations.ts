import { useCallback } from "react";
import { listCourierLocations } from "../api/client";
import { usePolling } from "./usePolling";
import type { CourierLocation } from "../types";

export function useCourierLocations(token: string, intervalMs: number): CourierLocation[] {
  const fetcher = useCallback(() => listCourierLocations(token), [token]);
  const { data } = usePolling(fetcher, intervalMs);
  return data ?? [];
}
