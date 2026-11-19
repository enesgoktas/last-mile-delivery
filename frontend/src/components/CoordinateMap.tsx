import { useEffect, useMemo, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { resolveDispatcherMapStyleUrl } from "../config/map";
import type { Courier, CourierLocation, Depot, Route, RouteGeometryResponse, RouteStop } from "../types";
import { loadMapLibreRuntime, type MapLibreRuntime } from "./maplibreRuntime";

type CoordinateMapProps = {
  couriers: Courier[];
  routes: Route[];
  depots?: Depot[];
  selectedRouteId?: string | null;
  selectedRouteGeometry?: RouteGeometryResponse | null;
  liveCourierLocations?: CourierLocation[];
  routeRoadGeometries?: Map<string, number[][]>;
  height?: number;
  onCourierClick?: (courierId: string) => void;
};

type LineFeature = {
  type: "Feature";
  geometry: {
    type: "LineString";
    coordinates: number[][];
  };
  properties: Record<string, unknown>;
};

type PointFeature = {
  type: "Feature";
  geometry: {
    type: "Point";
    coordinates: number[];
  };
  properties: Record<string, unknown>;
};

type FeatureCollection<T> = {
  type: "FeatureCollection";
  features: T[];
};

const ROUTE_SOURCE_ID = "routes-source";
const SELECTED_ROUTE_SOURCE_ID = "selected-route-source";
const STOPS_SOURCE_ID = "stops-source";
const COURIERS_SOURCE_ID = "couriers-source";
const DEPOTS_SOURCE_ID = "depots-source";

const ROUTE_LAYER_ID = "routes-layer";
const SELECTED_ROUTE_LAYER_ID = "selected-route-layer";
const STOPS_LAYER_ID = "stops-layer";
const STOPS_LABEL_LAYER_ID = "stops-label-layer";
const COURIERS_LAYER_ID = "couriers-layer";
const COURIERS_LABEL_LAYER_ID = "couriers-label-layer";
const DEPOTS_LAYER_ID = "depots-layer";
const DEPOTS_LABEL_LAYER_ID = "depots-label-layer";

const FALLBACK_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const FALLBACK_ROUTE_COLORS = ["#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#7c3aed", "#0ea5e9", "#d946ef"];

function resolveDefaultCenter(): [number, number] {
  const raw = import.meta.env.VITE_MAP_DEFAULT_CENTER as string | undefined;
  if (raw) {
    const parts = raw.split(",").map(Number);
    if (parts.length === 2 && parts.every((n) => !isNaN(n))) {
      return [parts[0], parts[1]];
    }
  }
  return [0, 20];
}

const DEFAULT_MAP_CENTER = resolveDefaultCenter();

function toLineCoordinates(value: unknown): number[][] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const coords: number[][] = [];
  for (const point of value) {
    if (!Array.isArray(point) || point.length < 2) {
      continue;
    }
    const lon = Number(point[0]);
    const lat = Number(point[1]);
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
      continue;
    }
    coords.push([lon, lat]);
  }
  return coords.length >= 2 ? coords : null;
}

function sortedStops(stops: RouteStop[]): RouteStop[] {
  return [...stops].sort((a, b) => a.sequence_number - b.sequence_number);
}

function buildBaseRouteLines(
  routes: Route[],
  courierById: Map<string, Courier>,
  selectedRouteId: string | null | undefined,
  routeRoadGeometries?: Map<string, number[][]>,
): FeatureCollection<LineFeature> {
  const features: LineFeature[] = [];
  routes.forEach((route, index) => {
    const orderedStops = sortedStops(route.stops);
    if (!orderedStops.length) {
      return;
    }

    const roadCoords = routeRoadGeometries?.get(route.id);
    if (roadCoords && roadCoords.length >= 2) {
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: roadCoords },
        properties: {
          routeId: route.id,
          selected: selectedRouteId === route.id,
          color: FALLBACK_ROUTE_COLORS[index % FALLBACK_ROUTE_COLORS.length],
        },
      });
      return;
    }

    const coordinates: number[][] = orderedStops.map((stop) => [stop.lon, stop.lat]);
    if (coordinates.length < 2) {
      return;
    }
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates },
      properties: {
        routeId: route.id,
        selected: selectedRouteId === route.id,
        color: FALLBACK_ROUTE_COLORS[index % FALLBACK_ROUTE_COLORS.length],
      },
    });
  });
  return { type: "FeatureCollection", features };
}

function buildSelectedRouteLines(
  selectedRouteId: string | null | undefined,
  selectedRouteGeometry: RouteGeometryResponse | null | undefined,
  routes: Route[],
  courierById: Map<string, Courier>,
): FeatureCollection<LineFeature> {
  if (!selectedRouteId) {
    return { type: "FeatureCollection", features: [] };
  }

  if (selectedRouteGeometry && selectedRouteGeometry.route_id === selectedRouteId) {
    const geometryFeatures = selectedRouteGeometry.geometry.features
      .map((feature) => toLineCoordinates(feature.geometry.coordinates))
      .filter((coords): coords is number[][] => coords !== null)
      .map(
        (coordinates): LineFeature => ({
          type: "Feature",
          geometry: { type: "LineString", coordinates },
          properties: {
            routeId: selectedRouteId,
            source: selectedRouteGeometry.source,
          },
        }),
      );
    if (geometryFeatures.length > 0) {
      return { type: "FeatureCollection", features: geometryFeatures };
    }
  }

  const selectedRoute = routes.find((route) => route.id === selectedRouteId);
  if (!selectedRoute) {
    return { type: "FeatureCollection", features: [] };
  }
  const orderedStops = sortedStops(selectedRoute.stops);
  if (!orderedStops.length) {
    return { type: "FeatureCollection", features: [] };
  }

  const coordinates: number[][] = orderedStops.map((stop) => [stop.lon, stop.lat]);
  if (coordinates.length < 2) {
    return { type: "FeatureCollection", features: [] };
  }

  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "LineString", coordinates },
        properties: {
          routeId: selectedRouteId,
          source: "fallback",
        },
      },
    ],
  };
}

function buildStopPoints(routes: Route[], selectedRouteId: string | null | undefined): FeatureCollection<PointFeature> {
  const features: PointFeature[] = [];
  routes.forEach((route) => {
    sortedStops(route.stops).forEach((stop) => {
      features.push({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [stop.lon, stop.lat],
        },
        properties: {
          routeId: route.id,
          sequence: stop.sequence_number,
          stopType: stop.stop_type,
          stopStatus: stop.status,
          selected: selectedRouteId === route.id,
        },
      });
    });
  });
  return { type: "FeatureCollection", features };
}

function buildCourierPoints(
  couriers: Courier[],
  liveCourierLocations: CourierLocation[] | undefined,
): FeatureCollection<PointFeature> {
  const byCourierId = new Map((liveCourierLocations ?? []).map((location) => [location.courier_id, location]));
  const features: PointFeature[] = [];
  couriers.forEach((courier) => {
    const live = byCourierId.get(courier.id);
    features.push({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [live?.lon ?? courier.depot_lon, live?.lat ?? courier.depot_lat],
      },
      properties: {
        courierId: courier.id,
        vehicleType: courier.vehicle_type,
        availability: courier.availability_status,
      },
    });
  });
  return { type: "FeatureCollection", features };
}

function buildDepotPoints(depots: Depot[]): FeatureCollection<PointFeature> {
  return {
    type: "FeatureCollection",
    features: depots.filter(d => d.is_active).map((d) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [d.lon, d.lat] },
      properties: { depotId: d.id, name: d.name },
    })),
  };
}

function ensureGeoJsonSource(
  map: MapLibreMap,
  sourceId: string,
  featureCollection: FeatureCollection<LineFeature> | FeatureCollection<PointFeature>,
) {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined;
  if (!source) {
    map.addSource(sourceId, {
      type: "geojson",
      data: featureCollection as never,
    });
    return;
  }
  source.setData(featureCollection as never);
}

function ensureLayers(map: MapLibreMap) {
  if (!map.getLayer(ROUTE_LAYER_ID)) {
    map.addLayer({
      id: ROUTE_LAYER_ID,
      type: "line",
      source: ROUTE_SOURCE_ID,
      paint: {
        "line-color": ["coalesce", ["get", "color"], "#64748b"],
        "line-width": ["case", ["==", ["get", "selected"], true], 4.0, 3.0],
        "line-opacity": ["case", ["==", ["get", "selected"], true], 0.9, 0.75],
      } as never,
    });
  }
  if (!map.getLayer(SELECTED_ROUTE_LAYER_ID)) {
    map.addLayer({
      id: SELECTED_ROUTE_LAYER_ID,
      type: "line",
      source: SELECTED_ROUTE_SOURCE_ID,
      paint: {
        "line-color": "#0b5fff",
        "line-width": 4.8,
        "line-opacity": 0.96,
      },
    });
  }
  if (!map.getLayer(STOPS_LAYER_ID)) {
    map.addLayer({
      id: STOPS_LAYER_ID,
      type: "circle",
      source: STOPS_SOURCE_ID,
      paint: {
        "circle-radius": ["case", ["==", ["get", "selected"], true], 5.6, 4.6],
        "circle-color": [
          "case",
          ["==", ["get", "stopStatus"], "FAILED"], "#b42318",
          ["==", ["get", "stopStatus"], "COMPLETED"], "#0f766e",
          // EV charging stops render in emerald so dispatchers can spot
          // them at a glance against the pickup/delivery palette.
          ["==", ["get", "stopType"], "CHARGING"], "#059669",
          ["==", ["get", "stopType"], "PICKUP"], "#0b5fff",
          "#f97316",
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.2,
      } as never,
    });
  }
  if (!map.getLayer(STOPS_LABEL_LAYER_ID)) {
    map.addLayer({
      id: STOPS_LABEL_LAYER_ID,
      type: "symbol",
      source: STOPS_SOURCE_ID,
      layout: {
        "text-field": ["to-string", ["get", "sequence"]],
        "text-size": 10,
        "text-offset": [0, 1.4],
      } as never,
      paint: {
        "text-color": "#334155",
      },
    });
  }
  if (!map.getLayer(COURIERS_LAYER_ID)) {
    map.addLayer({
      id: COURIERS_LAYER_ID,
      type: "circle",
      source: COURIERS_SOURCE_ID,
      paint: {
        "circle-radius": 6.8,
        "circle-color": [
          "case",
          ["==", ["get", "availability"], "ONLINE"], "#0f766e",
          ["==", ["get", "availability"], "ON_BREAK"], "#c97a00",
          "#9ca3af",
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      } as never,
    });
  }
  if (!map.getLayer(COURIERS_LABEL_LAYER_ID)) {
    map.addLayer({
      id: COURIERS_LABEL_LAYER_ID,
      type: "symbol",
      source: COURIERS_SOURCE_ID,
      layout: {
        "text-field": ["get", "vehicleType"],
        "text-size": 11,
        "text-offset": [0, -1.2],
      } as never,
      paint: {
        "text-color": "#334155",
      },
    });
  }
  if (!map.getLayer(DEPOTS_LAYER_ID) && map.getSource(DEPOTS_SOURCE_ID)) {
    map.addLayer({
      id: DEPOTS_LAYER_ID,
      type: "circle",
      source: DEPOTS_SOURCE_ID,
      paint: {
        "circle-radius": 8,
        "circle-color": "#4f46e5",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2.5,
      },
    });
  }
  if (!map.getLayer(DEPOTS_LABEL_LAYER_ID) && map.getSource(DEPOTS_SOURCE_ID)) {
    map.addLayer({
      id: DEPOTS_LABEL_LAYER_ID,
      type: "symbol",
      source: DEPOTS_SOURCE_ID,
      layout: {
        "text-field": ["get", "name"],
        "text-size": 11,
        "text-offset": [0, -1.6],
        "text-anchor": "bottom",
      } as never,
      paint: {
        "text-color": "#4f46e5",
        "text-halo-color": "#ffffff",
        "text-halo-width": 1,
      },
    });
  }
}

function fitMapToData(
  map: MapLibreMap,
  collections: Array<FeatureCollection<LineFeature> | FeatureCollection<PointFeature>>,
) {
  const points: number[][] = [];
  collections.forEach((collection) => {
    collection.features.forEach((feature) => {
      if (feature.geometry.type === "Point") {
        points.push(feature.geometry.coordinates);
      } else {
        feature.geometry.coordinates.forEach((point) => points.push(point));
      }
    });
  });
  if (!points.length) {
    map.easeTo({ center: DEFAULT_MAP_CENTER, zoom: 2, duration: 500 });
    return;
  }
  let minLon = points[0][0];
  let minLat = points[0][1];
  let maxLon = points[0][0];
  let maxLat = points[0][1];
  points.forEach(([lon, lat]) => {
    minLon = Math.min(minLon, lon);
    minLat = Math.min(minLat, lat);
    maxLon = Math.max(maxLon, lon);
    maxLat = Math.max(maxLat, lat);
  });

  map.fitBounds(
    [
      [minLon, minLat],
      [maxLon, maxLat],
    ],
    {
      padding: 52,
      duration: 550,
      maxZoom: 14,
    },
  );
}

async function createMapWithStyleFallback(maplibregl: MapLibreRuntime, container: HTMLDivElement) {
  const styleCandidates = Array.from(new Set([resolveDispatcherMapStyleUrl(), FALLBACK_STYLE_URL]));
  let lastError: unknown = null;

  for (const style of styleCandidates) {
    try {
      const map = await new Promise<MapLibreMap>((resolve, reject) => {
        const instance = new maplibregl.Map({
          container,
          style,
          center: DEFAULT_MAP_CENTER,
          zoom: 2,
        });

        let settled = false;
        const timeout = window.setTimeout(() => {
          if (settled) {
            return;
          }
          settled = true;
          instance.remove();
          reject(new Error(`Map style initialization timed out (${style})`));
        }, 9000);

        instance.once("load", () => {
          if (settled) {
            return;
          }
          settled = true;
          window.clearTimeout(timeout);
          resolve(instance);
        });
      });

      return map;
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError ?? new Error("Map initialization failed");
}

export function CoordinateMap({
  couriers,
  routes,
  depots,
  selectedRouteId,
  selectedRouteGeometry,
  liveCourierLocations,
  routeRoadGeometries,
  height,
  onCourierClick,
}: CoordinateMapProps) {
  const fillContainer = height === undefined || height === null;
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const fittedSelectionRef = useRef<string | null>(null);
  const courierClickRef = useRef(onCourierClick);
  courierClickRef.current = onCourierClick;

  const [mapStatus, setMapStatus] = useState<"loading" | "ready" | "error">("loading");
  const [mapError, setMapError] = useState<string | null>(null);
  const prevRouteCountRef = useRef(0);

  const courierById = useMemo(() => new Map(couriers.map((courier) => [courier.id, courier])), [couriers]);
  const routeLines = useMemo(
    () => buildBaseRouteLines(routes, courierById, selectedRouteId, routeRoadGeometries),
    [courierById, routes, selectedRouteId, routeRoadGeometries],
  );
  const selectedRouteLines = useMemo(
    () => buildSelectedRouteLines(selectedRouteId, selectedRouteGeometry, routes, courierById),
    [courierById, routes, selectedRouteGeometry, selectedRouteId],
  );
  const stopPoints = useMemo(() => buildStopPoints(routes, selectedRouteId), [routes, selectedRouteId]);
  const courierPoints = useMemo(
    () => buildCourierPoints(couriers, liveCourierLocations),
    [couriers, liveCourierLocations],
  );
  const depotPoints = useMemo(
    () => buildDepotPoints(depots ?? []),
    [depots],
  );

  useEffect(() => {
    if (import.meta.env.VITEST) {
      return;
    }
    if (!mapContainerRef.current || mapRef.current) {
      return;
    }

    let cancelled = false;
    setMapStatus("loading");
    setMapError(null);

    const setup = async () => {
      try {
        const maplibregl = await loadMapLibreRuntime();
        if (cancelled || !mapContainerRef.current) {
          return;
        }

        const map = await createMapWithStyleFallback(maplibregl, mapContainerRef.current);
        if (cancelled) {
          map.remove();
          return;
        }

        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        ensureGeoJsonSource(map, ROUTE_SOURCE_ID, { type: "FeatureCollection", features: [] });
        ensureGeoJsonSource(map, SELECTED_ROUTE_SOURCE_ID, { type: "FeatureCollection", features: [] });
        ensureGeoJsonSource(map, STOPS_SOURCE_ID, { type: "FeatureCollection", features: [] });
        ensureGeoJsonSource(map, COURIERS_SOURCE_ID, { type: "FeatureCollection", features: [] });
        ensureGeoJsonSource(map, DEPOTS_SOURCE_ID, { type: "FeatureCollection", features: [] });
        ensureLayers(map);

        map.on("click", COURIERS_LAYER_ID, (e) => {
          const feature = e.features?.[0];
          const courierId = feature?.properties?.courierId;
          if (courierId && courierClickRef.current) {
            courierClickRef.current(courierId);
          }
        });
        map.on("mouseenter", COURIERS_LAYER_ID, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", COURIERS_LAYER_ID, () => {
          map.getCanvas().style.cursor = "";
        });

        mapRef.current = map;
        setMapStatus("ready");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setMapStatus("error");
        setMapError(error instanceof Error ? error.message : "Map could not be initialized");
      }
    };

    void setup();

    return () => {
      cancelled = true;
      fittedSelectionRef.current = null;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
      setMapStatus("loading");
      setMapError(null);
    };
  }, []);

  useEffect(() => {
    if (mapStatus !== "ready" || !mapRef.current) {
      return;
    }
    const map = mapRef.current;

    ensureGeoJsonSource(map, ROUTE_SOURCE_ID, routeLines);
    ensureGeoJsonSource(map, SELECTED_ROUTE_SOURCE_ID, selectedRouteLines);
    ensureGeoJsonSource(map, STOPS_SOURCE_ID, stopPoints);
    ensureGeoJsonSource(map, COURIERS_SOURCE_ID, courierPoints);
    ensureGeoJsonSource(map, DEPOTS_SOURCE_ID, depotPoints);
    ensureLayers(map);

    const routeCount = routeLines.features.length;
    const routesJustArrived = prevRouteCountRef.current === 0 && routeCount > 0;
    prevRouteCountRef.current = routeCount;

    const fitKey = selectedRouteId ?? "__all__";
    if (fittedSelectionRef.current !== fitKey || routesJustArrived) {
      fittedSelectionRef.current = fitKey;
      fitMapToData(map, [selectedRouteLines.features.length ? selectedRouteLines : routeLines, stopPoints, courierPoints, depotPoints]);
    }
  }, [courierPoints, depotPoints, mapStatus, routeLines, selectedRouteId, selectedRouteLines, stopPoints]);

  const noData = stopPoints.features.length === 0 && courierPoints.features.length === 0;

  return (
    <div className={fillContainer ? "absolute inset-0" : "ops-map-wrapper"}>
      <div
        className={fillContainer ? "ops-map-fullheight w-full h-full" : "ops-map"}
        role="img"
        aria-label="Dispatch map"
        ref={mapContainerRef}
        style={fillContainer ? undefined : { height: height ?? 430 }}
      />
      {mapStatus !== "ready" ? (
        <div className="map-overlay">
          <p>{mapStatus === "loading" ? "Loading map…" : "Map failed to load"}</p>
          {mapError ? <p className="map-overlay-sub">{mapError}</p> : null}
        </div>
      ) : null}
      {mapStatus === "ready" && noData ? (
        <div className="map-overlay map-overlay-soft">
          <p>No route/courier points yet.</p>
          <p className="map-overlay-sub">Create or assign orders to see live overlays.</p>
        </div>
      ) : null}

      <div className="ops-map-legend">
        <span>
          <b>Blue line</b> = selected route
        </span>
        <span>
          <b>Green/amber/gray dots</b> = courier status
        </span>
        <span>
          <b>Blue/orange dots</b> = pickup/delivery stops
        </span>
        <span>
          <b>Indigo dots</b> = depots
        </span>
      </div>
    </div>
  );
}
