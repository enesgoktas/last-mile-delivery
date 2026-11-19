import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Fuel, Zap } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { usePolling } from "../hooks/usePolling";
import { useFeatures } from "../hooks/useFeatures";
import {
  listCouriersPaginated,
  createCourier,
  updateCourier,
  listUsers,
  listRoutes,
  listVehicleSpecs,
  listVehicleTags,
  getCourierOverrides,
  clearCourierConstraints,
  setCourierConstraints,
  setCourierTags,
  type ConstraintSet,
} from "../api/client";
import { StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { SlideOver } from "../components/ui/SlideOver";
import { Pagination } from "../components/Pagination";
import { Modal } from "../components/Modal";
import type {
  Courier,
  CourierCreate,
  CourierUpdate,
  VehicleSpec,
  VehicleTag,
  VehicleType,
  Route,
} from "../types";
import {
  FUEL_LABELS,
  VEHICLE_LABELS,
  VEHICLE_OPTIONS,
  vehicleFuelDefaultLabel,
  vehicleSpecLabel,
} from "../utils/vehicleFuel";

const POLL = 30_000;
const PAGE_SIZE = 25;

const inputCls = "w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-800 focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20";
const labelCls = "block text-xs font-medium text-slate-700 mb-1";

function FuelBadge({ fuelType }: { fuelType: string }) {
  const isElectric = fuelType === "ELECTRIC";
  const isHybrid = fuelType === "HYBRID";
  const cls = isElectric
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : isHybrid
      ? "bg-blue-50 text-blue-700 border-blue-200"
      : "bg-amber-50 text-amber-700 border-amber-200";
  const Icon = isElectric || isHybrid ? Zap : Fuel;
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded border ${cls}`}>
      <Icon size={9} />
      {FUEL_LABELS[fuelType as keyof typeof FUEL_LABELS] ?? fuelType}
    </span>
  );
}

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between py-2 border-b border-slate-50 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-xs font-medium text-slate-900">{value}</span>
    </div>
  );
}

type CourierFormSavePayload = {
  courierPayload: CourierCreate | CourierUpdate;
  courierId?: string;
  tagIds: string[];
  constraintOverrides: ConstraintSet;
};

function CourierForm({
  courier,
  users,
  vehicleSpecs,
  allVehicleTags,
  token,
  onSubmit,
  onClose,
}: {
  courier: Courier | null;
  users: { id: string; email: string }[];
  vehicleSpecs: VehicleSpec[];
  allVehicleTags: VehicleTag[];
  token: string;
  onSubmit: (data: CourierFormSavePayload) => Promise<void>;
  onClose: () => void;
}) {
  const isEdit = !!courier;
  const features = useFeatures();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    user_id: courier?.user_id ?? "",
    vehicle_type: (courier?.vehicle_type ?? "CAR_VAN") as VehicleType,
    vehicle_spec_id: courier?.vehicle_spec_id ?? "",
    max_weight_kg: courier?.max_weight_kg ?? 50,
    max_volume_cm3: courier?.max_volume_cm3 as number | undefined,
    max_packages: courier?.max_packages as number | undefined,
    depot_lat: courier?.depot_lat ?? 41.0082,
    depot_lon: courier?.depot_lon ?? 28.9784,
    depot_id: courier?.depot_id as string | undefined,
    working_hours_start: courier?.working_hours_start ?? "08:00",
    working_hours_end: courier?.working_hours_end ?? "18:00",
    // Phase 1
    max_passengers: courier?.max_passengers as number | undefined,
    // EV fields are conditional on `is_ev` — only persisted when the
    // checkbox is on. We treat any existing courier with a populated
    // `max_battery_range_km` as an EV when opening the form.
    max_battery_range_km: courier?.max_battery_range_km as number | undefined,
    battery_low_threshold_pct: courier?.battery_low_threshold_pct as number | undefined,
  });
  const [isEv, setIsEv] = useState<boolean>(
    Boolean(courier?.max_battery_range_km),
  );
  const [tagIds, setTagIds] = useState<string[]>(
    () => (courier?.vehicle_tags ?? []).map((t) => t.id),
  );
  const [constraintOverrides, setConstraintOverrides] = useState<ConstraintSet>({});
  const [constraintsLoading, setConstraintsLoading] = useState(false);

  useEffect(() => {
    if (!courier) {
      setTagIds([]);
      setConstraintOverrides({});
      setConstraintsLoading(false);
      return;
    }
    setTagIds((courier.vehicle_tags ?? []).map((t) => t.id));
    setConstraintsLoading(true);
    getCourierOverrides(token, courier.id)
      .then(setConstraintOverrides)
      .catch(() => undefined)
      .finally(() => setConstraintsLoading(false));
  }, [token, courier?.id]);

  function set(field: string, value: unknown) { setForm((p) => ({ ...p, [field]: value })); }

  // Filter specs by the selected vehicle type for a cleaner dropdown
  const filteredSpecs = useMemo(
    () => vehicleSpecs.filter((s) => s.vehicle_type === form.vehicle_type),
    [vehicleSpecs, form.vehicle_type],
  );

  // When vehicle type changes, clear spec if it no longer matches
  function handleVehicleTypeChange(vt: string) {
    set("vehicle_type", vt);
    const currentSpec = vehicleSpecs.find((s) => s.id === form.vehicle_spec_id);
    if (currentSpec && currentSpec.vehicle_type !== vt) {
      set("vehicle_spec_id", "");
    }
  }

  const selectedSpec = vehicleSpecs.find((s) => s.id === form.vehicle_spec_id) ?? null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const specId = form.vehicle_spec_id || undefined;
      // EV fields are persisted only when the 'electric' checkbox is on.
      // Dispatch assumes the courier starts the day at 100% battery, so
      // we don't ask the dispatcher for a current charge — the field is
      // initialised at 100 and only updated mid-day via the telemetry
      // endpoint POST /couriers/:id/battery.
      const evFields = isEv
        ? {
            max_battery_range_km: form.max_battery_range_km,
            current_battery_pct: 100,
            battery_low_threshold_pct: form.battery_low_threshold_pct ?? 20,
          }
        : {
            max_battery_range_km: null as number | null,
            current_battery_pct: null as number | null,
            battery_low_threshold_pct: null as number | null,
          };

      if (isEdit) {
        await onSubmit({
          courierPayload: {
            vehicle_type: form.vehicle_type,
            vehicle_spec_id: specId ?? null,
            max_weight_kg: form.max_weight_kg,
            max_volume_cm3: form.max_volume_cm3,
            max_packages: form.max_packages,
            depot_lat: form.depot_lat,
            depot_lon: form.depot_lon,
            depot_id: form.depot_id,
            working_hours_start: form.working_hours_start,
            working_hours_end: form.working_hours_end,
            max_passengers: form.max_passengers,
            ...evFields,
          } as CourierUpdate,
          courierId: courier!.id,
          tagIds,
          constraintOverrides,
        });
      } else {
        await onSubmit({
          courierPayload: {
            user_id: form.user_id,
            vehicle_type: form.vehicle_type,
            vehicle_spec_id: specId,
            max_weight_kg: form.max_weight_kg,
            max_volume_cm3: form.max_volume_cm3,
            max_packages: form.max_packages,
            depot_lat: form.depot_lat,
            depot_lon: form.depot_lon,
            depot_id: form.depot_id,
            working_hours_start: form.working_hours_start,
            working_hours_end: form.working_hours_end,
            max_passengers: form.max_passengers,
            ...evFields,
          } as CourierCreate,
          tagIds,
          constraintOverrides: {},
        });
      }
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open title={isEdit ? "Edit courier" : "Add courier"} onClose={onClose} wide>
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
        {!isEdit && (
          <div>
            <label className={labelCls}>User account</label>
            <select value={form.user_id} onChange={(e) => set("user_id", e.target.value)} className={inputCls} required>
              <option value="">Select a user…</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
            </select>
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Vehicle type</label>
            <select value={form.vehicle_type} onChange={(e) => handleVehicleTypeChange(e.target.value)} className={inputCls}>
              {VEHICLE_OPTIONS.map((v) => <option key={v} value={v}>{VEHICLE_LABELS[v]}</option>)}
            </select>
          </div>
          <div>
            <label className={labelCls}>Max weight (kg)</label>
            <input type="number" value={form.max_weight_kg} onChange={(e) => set("max_weight_kg", +e.target.value)} className={inputCls} required min={1} />
          </div>
        </div>

        {/* Vehicle spec selector */}
        <div>
          <label className={labelCls}>Vehicle spec <span className="text-slate-400 font-normal">(optional — enables fuel cost calculation)</span></label>
          <select
            value={form.vehicle_spec_id}
            onChange={(e) => set("vehicle_spec_id", e.target.value)}
            className={inputCls}
          >
            <option value="">Use {VEHICLE_LABELS[form.vehicle_type]} default ({vehicleFuelDefaultLabel(form.vehicle_type)})</option>
            {filteredSpecs.length === 0 && (
              <option disabled>No specs for this vehicle type yet</option>
            )}
            {filteredSpecs.map((s) => (
              <option key={s.id} value={s.id}>{vehicleSpecLabel(s)}</option>
            ))}
          </select>
          {selectedSpec && (
            <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-500">
              <FuelBadge fuelType={selectedSpec.fuel_type} />
              <span>{selectedSpec.fuel_efficiency_km_per_liter.toFixed(2)} km/L</span>
              {selectedSpec.co2_emissions_g_per_km != null && (
                <span>· {selectedSpec.co2_emissions_g_per_km.toFixed(0)} g CO₂/km</span>
              )}
            </div>
          )}
          {!selectedSpec && (
            <p className="mt-1.5 text-[11px] text-slate-500">
              Default fuel profile: {vehicleFuelDefaultLabel(form.vehicle_type)}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Max packages</label>
            <input type="number" value={form.max_packages ?? ""} onChange={(e) => set("max_packages", e.target.value ? +e.target.value : undefined)} className={inputCls} min={1} />
          </div>
          <div>
            <label className={labelCls}>Max volume (cm³)</label>
            <input type="number" value={form.max_volume_cm3 ?? ""} onChange={(e) => set("max_volume_cm3", e.target.value ? +e.target.value : undefined)} className={inputCls} min={1} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Work start</label>
            <input type="time" value={form.working_hours_start} onChange={(e) => set("working_hours_start", e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Work end</label>
            <input type="time" value={form.working_hours_end} onChange={(e) => set("working_hours_end", e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Depot lat</label>
            <input type="number" step="any" value={form.depot_lat} onChange={(e) => set("depot_lat", +e.target.value)} className={inputCls} required />
          </div>
          <div>
            <label className={labelCls}>Depot lon</label>
            <input type="number" step="any" value={form.depot_lon} onChange={(e) => set("depot_lon", +e.target.value)} className={inputCls} required />
          </div>
        </div>

        {features.rides_enabled && (
          <fieldset className="border border-slate-200 rounded-lg p-4">
            <legend className="text-xs font-medium text-slate-700 px-1">Passenger transport</legend>
            <div>
              <label className={labelCls}>Max passengers</label>
              <input
                type="number"
                value={form.max_passengers ?? ""}
                onChange={(e) => set("max_passengers", e.target.value ? +e.target.value : undefined)}
                className={inputCls}
                min={1}
                placeholder="Leave blank if this vehicle does not carry passengers"
              />
              <p className="mt-1.5 text-[11px] text-slate-500">
                Vehicles without a value cannot be assigned RIDE orders.
              </p>
            </div>
          </fieldset>
        )}

        {features.ev_battery_awareness_enabled && (
          <fieldset className="border border-slate-200 rounded-lg p-4">
            <legend className="text-xs font-medium text-slate-700 px-1">EV battery</legend>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={isEv}
                onChange={(e) => setIsEv(e.target.checked)}
              />
              This vehicle is electric
            </label>
            {isEv && (
              <div className="mt-3 space-y-3">
                <div>
                  <label className={labelCls}>Full battery range (km) <span className="text-red-500">*</span></label>
                  <input
                    type="number"
                    value={form.max_battery_range_km ?? ""}
                    onChange={(e) =>
                      set(
                        "max_battery_range_km",
                        e.target.value ? +e.target.value : undefined,
                      )
                    }
                    className={inputCls}
                    min={1}
                    required
                  />
                  <p className="mt-1.5 text-[11px] text-slate-500">
                    Range on a full charge. Dispatch assumes the vehicle starts
                    every shift at 100%; the kernel caps planned distance at
                    this value (refilled when a charging stop is inserted).
                  </p>
                </div>
                <div>
                  <label className={labelCls}>Low-charge alert threshold (%)</label>
                  <input
                    type="number"
                    value={form.battery_low_threshold_pct ?? ""}
                    onChange={(e) =>
                      set(
                        "battery_low_threshold_pct",
                        e.target.value ? +e.target.value : undefined,
                      )
                    }
                    className={inputCls}
                    min={0}
                    max={100}
                    placeholder="Default: 20"
                  />
                  <p className="mt-1.5 text-[11px] text-slate-500">
                    Surface this courier in the low-battery widget when their
                    reported charge dips below this %. Driver app sends
                    telemetry mid-shift.
                  </p>
                </div>
              </div>
            )}
          </fieldset>
        )}

        {isEdit && (
          <fieldset className="border border-slate-200 rounded-lg p-4">
            <legend className="text-xs font-medium text-slate-700 px-1">Constraint overrides</legend>
            {constraintsLoading ? (
              <p className="text-xs text-slate-400 mt-1">Loading…</p>
            ) : (
              <div className="space-y-3 mt-1">
                <p className="text-[11px] text-slate-400">Leave empty to use global defaults</p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] text-slate-500 mb-0.5">Max distance (km)</label>
                    <input
                      type="number"
                      step="any"
                      value={constraintOverrides.max_route_distance_km ?? ""}
                      placeholder="Global"
                      onChange={(event) =>
                        setConstraintOverrides((prev) => ({
                          ...prev,
                          max_route_distance_km: event.target.value ? Number(event.target.value) : null,
                        }))
                      }
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-500 mb-0.5">Max stops</label>
                    <input
                      type="number"
                      value={constraintOverrides.max_stops_per_route ?? ""}
                      placeholder="Global"
                      onChange={(event) =>
                        setConstraintOverrides((prev) => ({
                          ...prev,
                          max_stops_per_route: event.target.value ? Number(event.target.value) : null,
                        }))
                      }
                      className={inputCls}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      checked={constraintOverrides.allow_fragile ?? true}
                      onChange={(event) =>
                        setConstraintOverrides((prev) => ({
                          ...prev,
                          allow_fragile: event.target.checked,
                        }))
                      }
                    />
                    Allow fragile
                  </label>
                  <label className="flex items-center gap-2 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      checked={constraintOverrides.allow_temp_sensitive ?? true}
                      onChange={(event) =>
                        setConstraintOverrides((prev) => ({
                          ...prev,
                          allow_temp_sensitive: event.target.checked,
                        }))
                      }
                    />
                    Allow temp-sensitive
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => setConstraintOverrides({})}
                  className="text-xs text-accent-600 hover:text-accent-700"
                >
                  Reset to global
                </button>
              </div>
            )}
          </fieldset>
        )}

        <div>
          <label className={labelCls}>Vehicle tags</label>
          {allVehicleTags.length === 0 ? (
            <p className="text-[11px] text-slate-400">
              No tags defined yet — create some in Settings → Vehicle Tags.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {allVehicleTags.map((tag) => {
                const selected = tagIds.includes(tag.id);
                return (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() =>
                      setTagIds((prev) =>
                        selected ? prev.filter((id) => id !== tag.id) : [...prev, tag.id],
                      )
                    }
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all"
                    style={
                      selected
                        ? { backgroundColor: tag.color, borderColor: tag.color, color: "#fff" }
                        : { borderColor: tag.color, color: tag.color }
                    }
                  >
                    {tag.name}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving}>{saving ? "Saving…" : isEdit ? "Save changes" : "Create courier"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export function FleetPage() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const [page, setPage] = useState(0);
  const [selectedCourier, setSelectedCourier] = useState<Courier | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editTarget, setEditTarget] = useState<Courier | null>(null);
  const [users, setUsers] = useState<{ id: string; email: string }[]>([]);
  const [vehicleSpecs, setVehicleSpecs] = useState<VehicleSpec[]>([]);
  const [vehicleTags, setVehicleTags] = useState<VehicleTag[]>([]);

  const fetchCouriers = useCallback(() => listCouriersPaginated(token!, page * PAGE_SIZE, PAGE_SIZE), [token, page]);
  const fetchRoutes = useCallback(() => listRoutes(token!), [token]);

  const { data, isLoading, error, refetch } = usePolling(fetchCouriers, POLL);
  const { data: routes } = usePolling(fetchRoutes, POLL);
  const couriers = data?.data ?? [];
  const total = data?.total ?? 0;

  const online = useMemo(() => couriers.filter((c) => c.availability_status === "ONLINE").length, [couriers]);
  const onBreak = useMemo(() => couriers.filter((c) => c.availability_status === "ON_BREAK").length, [couriers]);
  const offline = useMemo(() => couriers.filter((c) => c.availability_status === "OFFLINE").length, [couriers]);

  const selectedCourierRoutes = useMemo(
    () => (routes ?? []).filter((r) => r.courier_id === selectedCourier?.id),
    [routes, selectedCourier],
  );
  const activeRoute = useMemo(
    () => selectedCourierRoutes.find((r) => r.status === "ACTIVE"),
    [selectedCourierRoutes],
  );

  // Fetch vehicle specs and tags once on mount
  useEffect(() => {
    if (!token) return;
    listVehicleSpecs(token)
      .then(setVehicleSpecs)
      .catch(() => setVehicleSpecs([]));
    listVehicleTags(token)
      .then(setVehicleTags)
      .catch(() => setVehicleTags([]));
  }, [token]);

  function getRouteProgress(route: Route) {
    const delivery = route.stops.filter(
      (s) =>
        s.stop_type !== "DEPOT_RETURN" &&
        s.stop_type !== "DEPOT_RELOAD" &&
        s.stop_type !== "CHARGING",
    );
    const completed = delivery.filter((s) => s.status === "COMPLETED").length;
    return { completed, total: delivery.length };
  }

  async function openCreate() {
    try { const u = await listUsers(token!); setUsers(u.map((x) => ({ id: x.id, email: x.email }))); }
    catch { setUsers([]); }
    setShowCreate(true);
  }

  async function handleCourierSave({
    courierPayload,
    courierId,
    tagIds,
    constraintOverrides,
  }: CourierFormSavePayload) {
    if (!token) return;
    try {
      if (courierId) {
        await updateCourier(token, courierId, courierPayload as CourierUpdate);

        const constraintPayload: Record<string, unknown> = {};
        for (const [key, value] of Object.entries(constraintOverrides)) {
          if (value !== null && value !== undefined) {
            constraintPayload[key] = value;
          }
        }
        if (Object.keys(constraintPayload).length > 0) {
          await setCourierConstraints(token, courierId, constraintPayload as ConstraintSet);
        } else {
          await clearCourierConstraints(token, courierId);
        }
        await setCourierTags(token, courierId, tagIds);

        addToast("Courier updated", "success");
        setSelectedCourier(null);
      } else {
        const created = await createCourier(token, courierPayload as CourierCreate);
        if (tagIds.length > 0) {
          await setCourierTags(token, created.id, tagIds);
        }
        addToast("Courier created", "success");
      }
      refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to save courier", "error");
      throw err;
    }
  }

  const spec = selectedCourier?.vehicle_spec ?? null;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 pt-5 pb-4 border-b border-slate-200 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <h1 className="text-base font-semibold text-slate-900">Fleet</h1>
          {!isLoading && !error && (
            <div className="flex items-center gap-2">
              {online > 0 && <span className="text-xs font-medium text-emerald-700 border border-emerald-200 bg-emerald-50 px-2 py-0.5 rounded">{online} online</span>}
              {onBreak > 0 && <span className="text-xs font-medium text-amber-700 border border-amber-200 bg-amber-50 px-2 py-0.5 rounded">{onBreak} on break</span>}
              {offline > 0 && <span className="text-xs font-medium text-slate-600 border border-slate-200 bg-slate-50 px-2 py-0.5 rounded">{offline} offline</span>}
            </div>
          )}
        </div>
        <Button variant="primary" size="sm" onClick={() => void openCreate()}>
          <Plus size={13} />
          Add Courier
        </Button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto px-6 py-4 thin-scrollbar">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-slate-400">Loading…</div>
        ) : error ? (
          <div className="py-4 text-sm text-red-600">Failed to load fleet.</div>
        ) : couriers.length === 0 ? (
          <div className="py-16 text-center text-sm text-slate-400">No couriers yet.</div>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">ID</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Vehicle</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Spec</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Status</th>
                <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Max weight</th>
                <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Capacity</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Hours</th>
              </tr>
            </thead>
            <tbody>
              {couriers.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelectedCourier(selectedCourier?.id === c.id ? null : c)}
                  className={`border-b border-slate-100 cursor-pointer transition-colors duration-100 ${
                    selectedCourier?.id === c.id ? "bg-orange-50" : "hover:bg-slate-50"
                  }`}
                >
                  <td className="py-2.5 px-3 font-mono text-xs text-slate-600">{c.id.slice(0, 8)}</td>
                  <td className="py-2.5 px-3 text-sm text-slate-800">{VEHICLE_LABELS[c.vehicle_type] ?? c.vehicle_type}</td>
                  <td className="py-2.5 px-3 text-xs text-slate-500">
                    {c.vehicle_spec ? (
                      <span className="flex items-center gap-1.5">
                        <span>{c.vehicle_spec.make} {c.vehicle_spec.model}{c.vehicle_spec.year ? ` '${String(c.vehicle_spec.year).slice(2)}` : ""}</span>
                        <FuelBadge fuelType={c.vehicle_spec.fuel_type} />
                      </span>
                    ) : (
                      <span className="text-slate-400">Default · {vehicleFuelDefaultLabel(c.vehicle_type)}</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3"><StatusBadge value={c.availability_status} /></td>
                  <td className="py-2.5 px-3 text-sm text-slate-700 text-right tabular-nums">{c.max_weight_kg} kg</td>
                  <td className="py-2.5 px-3 text-sm text-slate-500 text-right tabular-nums">{c.max_packages != null ? `${c.max_packages} pkg` : "—"}</td>
                  <td className="py-2.5 px-3 text-sm text-slate-500 tabular-nums">
                    {c.working_hours_start && c.working_hours_end ? `${c.working_hours_start} – ${c.working_hours_end}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {total > PAGE_SIZE && <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />}
      </div>

      {/* Detail drawer */}
      <SlideOver
        open={!!selectedCourier}
        onClose={() => setSelectedCourier(null)}
        title={selectedCourier ? VEHICLE_LABELS[selectedCourier.vehicle_type] ?? selectedCourier.vehicle_type : ""}
        subtitle={selectedCourier?.id.slice(0, 12)}
      >
        {selectedCourier && (
          <div className="px-5 py-4 space-y-5">
            <div className="flex items-center gap-2">
              <StatusBadge value={selectedCourier.availability_status} />
            </div>

            <div>
              <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Capacity</p>
              <FieldRow label="Max weight" value={`${selectedCourier.max_weight_kg} kg`} />
              <FieldRow label="Max packages" value={selectedCourier.max_packages ?? "—"} />
              <FieldRow label="Max volume" value={selectedCourier.max_volume_cm3 != null ? `${(selectedCourier.max_volume_cm3 / 1000).toFixed(0)} L` : "—"} />
            </div>

            <div>
              <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Schedule</p>
              <FieldRow label="Working hours" value={
                selectedCourier.working_hours_start && selectedCourier.working_hours_end
                  ? `${selectedCourier.working_hours_start} – ${selectedCourier.working_hours_end}`
                  : "Not set"
              } />
            </div>

            {/* Vehicle Spec section */}
            <div>
              <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Vehicle spec</p>
              {spec ? (
                <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2.5 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-800">
                      {spec.make} {spec.model}{spec.year ? ` (${spec.year})` : ""}
                    </span>
                    <FuelBadge fuelType={spec.fuel_type} />
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-slate-500">
                    <span>{spec.fuel_efficiency_km_per_liter.toFixed(2)} km/L</span>
                    {spec.co2_emissions_g_per_km != null && (
                      <span>{spec.co2_emissions_g_per_km.toFixed(0)} g CO₂/km</span>
                    )}
                  </div>
                  {spec.epa_vehicle_id && (
                    <p className="text-[10px] text-slate-400">Source: {spec.source ?? "EPA"}</p>
                  )}
                </div>
              ) : (
                <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2.5">
                  <p className="text-xs font-semibold text-slate-800">
                    {VEHICLE_LABELS[selectedCourier.vehicle_type]} default
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {vehicleFuelDefaultLabel(selectedCourier.vehicle_type)}
                  </p>
                </div>
              )}
            </div>

            {activeRoute ? (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Active route</p>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-mono text-slate-600">{activeRoute.id.slice(0, 8)}</span>
                  <StatusBadge value={activeRoute.status} />
                </div>
                {(() => {
                  const { completed, total: tot } = getRouteProgress(activeRoute);
                  return (
                    <>
                      <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                        <div className="h-full bg-accent-500 transition-all" style={{ width: tot > 0 ? `${(completed / tot) * 100}%` : "0%" }} />
                      </div>
                      <p className="text-[10px] text-slate-400 mt-1.5 tabular-nums">{completed}/{tot} stops · {activeRoute.planned_distance_km.toFixed(1)} km</p>
                    </>
                  );
                })()}
              </div>
            ) : (
              <p className="text-xs text-slate-400">No active route.</p>
            )}

            <Button
              variant="secondary"
              size="sm"
              className="w-full justify-center"
              onClick={() => { setEditTarget(selectedCourier); setSelectedCourier(null); }}
            >
              Edit courier
            </Button>
          </div>
        )}
      </SlideOver>

      {showCreate && token && (
        <CourierForm
          courier={null}
          users={users}
          vehicleSpecs={vehicleSpecs}
          allVehicleTags={vehicleTags}
          token={token}
          onSubmit={handleCourierSave}
          onClose={() => setShowCreate(false)}
        />
      )}
      {editTarget && token && (
        <CourierForm
          courier={editTarget}
          users={[]}
          vehicleSpecs={vehicleSpecs}
          allVehicleTags={vehicleTags}
          token={token}
          onSubmit={handleCourierSave}
          onClose={() => setEditTarget(null)}
        />
      )}
    </div>
  );
}
