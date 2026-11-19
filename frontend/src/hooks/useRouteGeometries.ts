import { useEffect, useRef, useState, useMemo } from "react";
import { getRouteGeometry } from "../api/client";
import type { Route } from "../types";

type RouteLineCoords = Map<string, number[][]>;

const ROUTE_GEOMETRY_CACHE_MAX_ENTRIES = 200;
const cache = new Map<string, number[][]>();

function touchCacheEntry(routeId: string, coords: number[][]): void {
  cache.delete(routeId);
  cache.set(routeId, coords);
  while (cache.size > ROUTE_GEOMETRY_CACHE_MAX_ENTRIES) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey === undefined) {
      break;
    }
    cache.delete(oldestKey);
  }
}

function routeFingerprint(route: Route): string {
  const stops = [...route.stops].sort((a, b) => a.sequence_number - b.sequence_number);
  return stops.map((s) => `${s.lon.toFixed(5)},${s.lat.toFixed(5)}`).join(";");
}

function extractFullRouteCoords(data: unknown): number[][] | null {
  const geom = (data as Record<string, unknown>)?.geometry as
    | { features?: Array<{ properties?: { kind?: string }; geometry?: { coordinates?: number[][] } }> }
    | undefined;
  if (!geom?.features) return null;
  const fullRoute = geom.features.find((f) => f.properties?.kind === "full_route");
  return fullRoute?.geometry?.coordinates ?? null;
}

export function useRouteGeometries(
  routes: Route[],
  token?: string | null,
): RouteLineCoords {
  const [geometries, setGeometries] = useState<RouteLineCoords>(new Map());
  const fetchedRef = useRef(new Map<string, string>());

  const routeFingerprints = useMemo(() => {
    const map = new Map<string, string>();
    for (const route of routes) {
      if (route.stops.length) {
        map.set(route.id, routeFingerprint(route));
      }
    }
    return map;
  }, [routes]);

  useEffect(() => {
    const activeRouteIds = new Set(routeFingerprints.keys());
    for (const routeId of fetchedRef.current.keys()) {
      if (!activeRouteIds.has(routeId)) {
        fetchedRef.current.delete(routeId);
      }
    }
    setGeometries((prev) => {
      let changed = false;
      const next = new Map<string, number[][]>();
      for (const [routeId, coords] of prev) {
        if (activeRouteIds.has(routeId)) {
          next.set(routeId, coords);
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });

    if (!token) return;
    let cancelled = false;

    async function fetchAll() {
      const toFetch: string[] = [];
      const fromCache = new Map<string, number[][]>();

      for (const [routeId, fp] of routeFingerprints) {
        const prevFp = fetchedRef.current.get(routeId);
        if (prevFp === fp) continue;

        if (prevFp !== undefined && prevFp !== fp) {
          cache.delete(routeId);
        }

        const cached = cache.get(routeId);
        if (cached) {
          touchCacheEntry(routeId, cached);
          fetchedRef.current.set(routeId, fp);
          fromCache.set(routeId, cached);
          continue;
        }
        toFetch.push(routeId);
      }

      if (fromCache.size > 0 && !cancelled) {
        setGeometries((prev) => {
          const next = new Map(prev);
          fromCache.forEach((coords, id) => next.set(id, coords));
          return next;
        });
      }

      if (!toFetch.length) return;

      const results = new Map<string, number[][]>();
      const batchSize = 4;
      for (let i = 0; i < toFetch.length; i += batchSize) {
        if (cancelled) return;
        const batch = toFetch.slice(i, i + batchSize);
        const fetched = await Promise.all(
          batch.map(async (routeId) => {
            try {
              const data = await getRouteGeometry(token!, routeId);
              const coords = extractFullRouteCoords(data);
              if (coords) {
                touchCacheEntry(routeId, coords);
                return { routeId, coords };
              }
            } catch {
              /* backend unavailable — skip this route */
            }
            return { routeId, coords: null };
          }),
        );
        for (const { routeId, coords } of fetched) {
          // Only mark as fetched when geometry actually came back.
          // Leaving it unmarked lets the next poll retry failed/empty fetches
          // (e.g. right after optimization when the route is brand new).
          if (coords) {
            const fp = routeFingerprints.get(routeId);
            if (fp) fetchedRef.current.set(routeId, fp);
            results.set(routeId, coords);
          }
        }
      }

      if (!cancelled && results.size > 0) {
        setGeometries((prev) => {
          const next = new Map(prev);
          results.forEach((coords, id) => next.set(id, coords));
          return next;
        });
      }
    }

    fetchAll();
    return () => {
      cancelled = true;
    };
  }, [routeFingerprints, token]);

  return geometries;
}
