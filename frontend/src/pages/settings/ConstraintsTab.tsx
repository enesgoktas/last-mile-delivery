import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import {
  getGlobalConstraints,
  setGlobalConstraints,
  type ConstraintSet,
} from "../../api/client";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const CONSTRAINT_FIELDS: {
  key: keyof ConstraintSet;
  label: string;
  type: "number" | "boolean";
  hint: string;
}[] = [
  {
    key: "max_route_distance_km",
    label: "Max Route Distance (km)",
    type: "number",
    hint: "Leave empty for unlimited",
  },
  {
    key: "max_stops_per_route",
    label: "Max Stops per Route",
    type: "number",
    hint: "Default: 25",
  },
  {
    key: "max_weight_kg",
    label: "Max Carrying Weight (kg)",
    type: "number",
    hint: "Default: 50",
  },
  {
    key: "max_volume_cm3",
    label: "Max Carrying Volume (cm³)",
    type: "number",
    hint: "Default: 500000",
  },
  {
    key: "max_packages",
    label: "Max Packages per Trip",
    type: "number",
    hint: "Default: 30",
  },
  {
    key: "max_trips_per_day",
    label: "Max Trips per Day",
    type: "number",
    hint: "Default: 3",
  },
  {
    key: "depot_reload_time_minutes",
    label: "Depot Reload Time (min)",
    type: "number",
    hint: "Default: 15",
  },
  {
    key: "max_shift_hours",
    label: "Max Shift Hours",
    type: "number",
    hint: "Leave empty to use working hours",
  },
  {
    key: "break_duration_minutes",
    label: "Break Duration (min)",
    type: "number",
    hint: "Default: 30",
  },
  {
    key: "break_earliest_offset_hours",
    label: "Break Earliest (hours)",
    type: "number",
    hint: "Default: 3.0",
  },
  {
    key: "break_latest_offset_hours",
    label: "Break Latest (hours)",
    type: "number",
    hint: "Default: 6.0",
  },
  {
    key: "break_required",
    label: "Break Required",
    type: "boolean",
    hint: "Whether drivers must take a break",
  },
  {
    key: "service_time_minutes",
    label: "Service Time per Stop (min)",
    type: "number",
    hint: "Default: 5",
  },
  {
    key: "allow_fragile",
    label: "Allow Fragile Items",
    type: "boolean",
    hint: "Can drivers carry fragile by default",
  },
  {
    key: "allow_temp_sensitive",
    label: "Allow Temp-Sensitive",
    type: "boolean",
    hint: "Can drivers carry temp-sensitive by default",
  },
];

export function ConstraintsTab() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const [constraints, setConstraints] = useState<ConstraintSet>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadConstraints = useCallback(async () => {
    try {
      setConstraints(await getGlobalConstraints(token!));
    } catch {
      addToast("Failed to load constraints", "error");
    } finally {
      setLoading(false);
    }
  }, [token, addToast]);

  useEffect(() => {
    loadConstraints();
  }, [loadConstraints]);

  async function handleSave() {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const field of CONSTRAINT_FIELDS) {
        const value = constraints[field.key];
        if (value !== undefined) {
          payload[field.key] = value;
        }
      }
      await setGlobalConstraints(token!, payload as ConstraintSet);
      addToast("Constraints saved", "success");
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Save failed", "error");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="p-12 text-center text-gray-400 text-sm">Loading...</div>;
  }

  return (
    <div className="max-w-2xl space-y-6">
      <p className="text-sm text-gray-500">
        Global constraints apply to all couriers by default. Override per-courier in the
        Couriers tab.
      </p>
      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
        {CONSTRAINT_FIELDS.map((field) => (
          <div key={field.key}>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {field.label}
            </label>
            {field.type === "boolean" ? (
              <div className="flex items-center gap-3">
                <button
                  onClick={() =>
                    setConstraints((prev) => ({ ...prev, [field.key]: true }))
                  }
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                    constraints[field.key] === true
                      ? "border-brand-300 bg-brand-50 text-brand-700"
                      : "border-gray-200 text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  Yes
                </button>
                <button
                  onClick={() =>
                    setConstraints((prev) => ({ ...prev, [field.key]: false }))
                  }
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                    constraints[field.key] === false
                      ? "border-red-300 bg-red-50 text-red-700"
                      : "border-gray-200 text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  No
                </button>
              </div>
            ) : (
              <input
                type="number"
                step="any"
                value={
                  typeof constraints[field.key] === "number"
                    ? (constraints[field.key] as number)
                    : ""
                }
                onChange={(event) =>
                  setConstraints((prev) => ({
                    ...prev,
                    [field.key]:
                      event.target.value === "" ? null : Number(event.target.value),
                  }))
                }
                className={SETTINGS_INPUT_CLASSNAME}
                placeholder={field.hint}
              />
            )}
            <p className="text-xs text-gray-400 mt-1">{field.hint}</p>
          </div>
        ))}
        <div className="pt-3 flex justify-end">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2.5 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save Constraints"}
          </button>
        </div>
      </div>
    </div>
  );
}
