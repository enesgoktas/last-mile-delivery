import { useEffect, useMemo, useState } from "react";
import { Battery, BatteryWarning } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { useFeatures } from "../hooks/useFeatures";
import { listCouriers } from "../api/client";
import type { Courier } from "../types";

/**
 * Surfaces couriers whose `current_battery_pct` is at or below their
 * `battery_low_threshold_pct` (default 20). Renders nothing when the
 * tenant doesn't have `ev_low_battery_alerts_enabled`, so existing
 * delivery-only tenants are unaffected.
 */
export function LowBatteryWidget() {
  const { token } = useAuth();
  const features = useFeatures();
  const [couriers, setCouriers] = useState<Courier[] | null>(null);

  useEffect(() => {
    if (!token || !features.ev_low_battery_alerts_enabled) return;
    let cancelled = false;
    listCouriers(token)
      .then((c) => {
        if (!cancelled) setCouriers(c as Courier[]);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token, features.ev_low_battery_alerts_enabled]);

  const lowBattery = useMemo(() => {
    if (!couriers) return [];
    return couriers
      .filter((c) => {
        const pct = c.current_battery_pct;
        const threshold = c.battery_low_threshold_pct ?? 20;
        return typeof pct === "number" && pct <= threshold;
      })
      .sort(
        (a, b) =>
          (a.current_battery_pct ?? 0) - (b.current_battery_pct ?? 0),
      );
  }, [couriers]);

  if (!features.ev_low_battery_alerts_enabled) return null;
  if (couriers === null) return null;
  if (lowBattery.length === 0) return null;

  return (
    <div className="border border-amber-200 rounded-md bg-amber-50/60 px-4 py-3 mb-4">
      <div className="flex items-center gap-2 mb-2">
        <BatteryWarning size={14} className="text-amber-600" />
        <h3 className="text-sm font-semibold text-amber-900">
          {lowBattery.length} vehicle
          {lowBattery.length === 1 ? "" : "s"} below charge threshold
        </h3>
      </div>
      <div className="space-y-1">
        {lowBattery.map((c) => (
          <div
            key={c.id}
            className="flex items-center justify-between text-xs text-amber-900"
          >
            <span>
              <Battery size={11} className="inline mr-1.5 text-amber-600" />
              Vehicle {c.id.slice(0, 8)}
            </span>
            <span className="tabular-nums font-medium">
              {c.current_battery_pct}% (threshold {c.battery_low_threshold_pct ?? 20}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
