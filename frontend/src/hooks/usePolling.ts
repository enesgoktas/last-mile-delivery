import { useEffect, useRef, useState } from "react";

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
) {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  const inFlightRef = useRef(false);
  const pendingRunRef = useRef(false);
  const runRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      enabledRef.current = false;
      pendingRunRef.current = false;
    };
  }, []);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      pendingRunRef.current = false;
      setData(null);
      setIsLoading(false);
      return undefined;
    }

    pendingRunRef.current = false;
    setIsLoading(true);

    runRef.current = async () => {
      if (!enabledRef.current) {
        return;
      }
      if (inFlightRef.current) {
        pendingRunRef.current = true;
        return;
      }

      inFlightRef.current = true;
      try {
        do {
          pendingRunRef.current = false;
          try {
            const next = await fetcherRef.current();
            if (mountedRef.current && enabledRef.current) {
              setData(next);
              setError(null);
            }
          } catch (err) {
            if (mountedRef.current && enabledRef.current) {
              // Intentionally retain stale `data` on error so consumers can keep
              // showing the last known values alongside the error banner rather
              // than switching to a blank / loading state mid-session.
              setError(err instanceof Error ? err.message : "Polling failed");
            }
          }
        } while (enabledRef.current && pendingRunRef.current);
      } finally {
        inFlightRef.current = false;
        if (mountedRef.current && enabledRef.current) {
          setIsLoading(false);
        }
      }
    };

    void runRef.current();
    const timer = setInterval(() => {
      void runRef.current();
    }, intervalMs);
    return () => {
      clearInterval(timer);
    };
  }, [enabled, fetcher, intervalMs]);

  useEffect(() => {
    if (!enabled || reloadNonce === 0) {
      return;
    }
    setIsLoading(true);
    void runRef.current();
  }, [enabled, reloadNonce]);

  const refetch = () => setReloadNonce((v) => v + 1);

  return { data, isLoading, error, refetch };
}
