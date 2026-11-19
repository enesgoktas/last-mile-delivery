import { createContext, useContext, useEffect, useState } from "react";
import { useAuth } from "../state/AuthContext";
import { getMyFeatureFlags } from "../api/client";
import type { FeatureFlags } from "../types";

// Reasonable defaults — match backend FeatureFlags() so the dashboard
// renders correctly while we're loading or when the API is unreachable.
const DEFAULT_FLAGS: FeatureFlags = {
  deliveries_enabled: true,
  rides_enabled: false,
  ride_pickup_buffer_enabled: false,
  ride_strict_lateness_enabled: false,
  ride_airport_features_enabled: false,
  ride_no_show_handling_enabled: false,
  ev_battery_awareness_enabled: false,
  ev_low_battery_alerts_enabled: false,
  ev_auto_charging_enabled: false,
  multi_depot_vehicles_enabled: false,
  multi_trip_enabled: true,
  traffic_aware_routing_enabled: true,
};

type FeaturesContextValue = {
  features: FeatureFlags;
  loaded: boolean;
};

const FeaturesContext = createContext<FeaturesContextValue>({
  features: DEFAULT_FLAGS,
  loaded: false,
});

export function FeaturesProvider({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  const [features, setFeatures] = useState<FeatureFlags>(DEFAULT_FLAGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!token) {
      setFeatures(DEFAULT_FLAGS);
      setLoaded(false);
      return;
    }
    let cancelled = false;
    getMyFeatureFlags(token)
      .then((f) => {
        if (!cancelled) {
          setFeatures(f);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true); // keep defaults
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <FeaturesContext.Provider value={{ features, loaded }}>
      {children}
    </FeaturesContext.Provider>
  );
}

export function useFeatures(): FeatureFlags {
  return useContext(FeaturesContext).features;
}
