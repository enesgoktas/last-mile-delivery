import { useCallback, useEffect, useState } from "react";
import { getOptimizationRunPreview } from "../api/client";
import type { OptimizationRunPreview } from "../types";

type UseOptimizationRunPreviewArgs = {
  token: string;
  runId: string | null;
  enabled: boolean;
};

export function useOptimizationRunPreview({
  token,
  runId,
  enabled,
}: UseOptimizationRunPreviewArgs) {
  const [preview, setPreview] = useState<OptimizationRunPreview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPreview = useCallback(async () => {
    if (!enabled || !runId) {
      setPreview(null);
      setError(null);
      return null;
    }

    setIsLoading(true);
    setError(null);
    try {
      const nextPreview = await getOptimizationRunPreview(token, runId);
      setPreview(nextPreview);
      return nextPreview;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load optimization preview";
      setError(message);
      setPreview(null);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [enabled, runId, token]);

  useEffect(() => {
    void fetchPreview();
  }, [fetchPreview]);

  return {
    preview,
    isLoading,
    error,
    refetch: fetchPreview,
  };
}
