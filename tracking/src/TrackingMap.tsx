import { useEffect, useRef, useState } from "react";
import maplibregl, { LngLatBoundsLike } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

type Props = {
  courierLat?: number | null;
  courierLon?: number | null;
  deliveryLat: number;
  deliveryLon: number;
};

const DEFAULT_ZOOM = 13;

// Allow the tile style to be configured per-deployment; fall back to the
// MapLibre demo endpoint only in development (it has no SLA).
const STYLE_URL =
  (import.meta.env.VITE_MAP_STYLE_URL as string | undefined)?.trim() ||
  "https://demotiles.maplibre.org/style.json";

export function TrackingMap({ courierLat, courierLon, deliveryLat, deliveryLon }: Props) {
  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const courierMarkerRef = useRef<maplibregl.Marker | null>(null);
  const deliveryMarkerRef = useRef<maplibregl.Marker | null>(null);

  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);

  // Initialise the map exactly once — never re-run when coords change.
  // Marker positions are updated by the second effect.
  useEffect(() => {
    if (!mapEl.current || mapRef.current) {
      return;
    }

    const center: [number, number] =
      courierLat != null && courierLon != null
        ? [courierLon, courierLat]
        : [deliveryLon, deliveryLat];

    const map = new maplibregl.Map({
      container: mapEl.current,
      style: STYLE_URL,
      center,
      zoom: DEFAULT_ZOOM,
      attributionControl: { compact: true },
    });

    map.on("load", () => setMapReady(true));
    map.on("error", (e) => {
      const msg = (e as { error?: { message?: string } }).error?.message ?? "Map failed to load";
      setMapError(msg);
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current = map;

    return () => {
      courierMarkerRef.current?.remove();
      deliveryMarkerRef.current?.remove();
      mapRef.current?.remove();
      courierMarkerRef.current = null;
      deliveryMarkerRef.current = null;
      mapRef.current = null;
      setMapReady(false);
      setMapError(null);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — map is created once per mount

  // Keep markers in sync with the latest prop values.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) {
      return;
    }

    if (!deliveryMarkerRef.current) {
      deliveryMarkerRef.current = new maplibregl.Marker({ color: "#0b5fff" })
        .setLngLat([deliveryLon, deliveryLat])
        .setPopup(new maplibregl.Popup({ closeButton: false }).setText("Delivery destination"))
        .addTo(map);
    } else {
      deliveryMarkerRef.current.setLngLat([deliveryLon, deliveryLat]);
    }

    if (courierLat != null && courierLon != null) {
      if (!courierMarkerRef.current) {
        courierMarkerRef.current = new maplibregl.Marker({ color: "#f97316" })
          .setLngLat([courierLon, courierLat])
          .setPopup(new maplibregl.Popup({ closeButton: false }).setText("Courier location"))
          .addTo(map);
      } else {
        courierMarkerRef.current.setLngLat([courierLon, courierLat]);
      }
    } else {
      courierMarkerRef.current?.remove();
      courierMarkerRef.current = null;
    }

    if (courierLat != null && courierLon != null) {
      const bounds: LngLatBoundsLike = [
        [Math.min(courierLon, deliveryLon), Math.min(courierLat, deliveryLat)],
        [Math.max(courierLon, deliveryLon), Math.max(courierLat, deliveryLat)],
      ];
      map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 500 });
      return;
    }

    map.easeTo({ center: [deliveryLon, deliveryLat], zoom: DEFAULT_ZOOM, duration: 500 });
  }, [courierLat, courierLon, deliveryLat, deliveryLon, mapReady]);

  return (
    <div className="tracking-map-wrapper">
      {!mapReady && !mapError && (
        <div className="tracking-map-overlay" aria-live="polite">
          <p className="muted">Loading map…</p>
        </div>
      )}
      {mapError && (
        <div className="tracking-map-overlay tracking-map-error" aria-live="assertive">
          <p className="error">Map could not be loaded. Please try refreshing the page.</p>
        </div>
      )}
      <div className="tracking-map" ref={mapEl} aria-label="Live delivery map" role="img" />
    </div>
  );
}
