import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { useFeatures } from "../../hooks/useFeatures";
import { usePolling } from "../../hooks/usePolling";
import {
  clearCourierConstraints,
  createCourier,
  getCourierOverrides,
  listCouriersPaginated,
  listDepots,
  listVehicleSpecs,
  listVehicleTags,
  setCourierConstraints,
  setCourierTags,
  type ConstraintSet,
  updateCourier,
} from "../../api/client";
import { Modal } from "../../components/Modal";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import type { Courier, CourierUpdate, Depot, VehicleSpec, VehicleTag, VehicleType } from "../../types";
import {
  VEHICLE_LABELS,
  VEHICLE_OPTIONS,
  vehicleFuelDefaultLabel,
  vehicleSpecLabel,
} from "../../utils/vehicleFuel";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const PAGE_SIZE = 25;

export function CouriersTab() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const [page, setPage] = useState(0);

  const fetchCouriers = useCallback(
    () => listCouriersPaginated(token!, page * PAGE_SIZE, PAGE_SIZE),
    [token, page],
  );
  const { data: result, isLoading, refetch } = usePolling(fetchCouriers, 60_000);
  const couriers = result?.data ?? null;
  const total = result?.total ?? 0;

  const fetchDepots = useCallback(() => listDepots(token!), [token]);
  const { data: depots } = usePolling(fetchDepots, 120_000);

  useEffect(() => {
    if (!token) return;
    listVehicleSpecs(token).then(setVehicleSpecs).catch(() => setVehicleSpecs([]));
    listVehicleTags(token).then(setAllVehicleTags).catch(() => setAllVehicleTags([]));
  }, [token]);

  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState<Courier | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [userId, setUserId] = useState("");
  const [vehicleType, setVehicleType] = useState<VehicleType>("CAR_VAN");
  const [maxWeight, setMaxWeight] = useState("50");
  const [maxVolume, setMaxVolume] = useState("");
  const [maxPkgs, setMaxPkgs] = useState("");
  const [depotId, setDepotId] = useState("");
  const [depotLat, setDepotLat] = useState("");
  const [depotLon, setDepotLon] = useState("");
  const [workStart, setWorkStart] = useState("08:00");
  const [workEnd, setWorkEnd] = useState("18:00");

  const [vehicleSpecs, setVehicleSpecs] = useState<VehicleSpec[]>([]);
  const [vehicleSpecId, setVehicleSpecId] = useState("");
  const [editVehicleSpecId, setEditVehicleSpecId] = useState("");

  const [editVehicle, setEditVehicle] = useState<VehicleType>("CAR_VAN");
  const [editMaxWeight, setEditMaxWeight] = useState("");
  const [editMaxVolume, setEditMaxVolume] = useState("");
  const [editMaxPkgs, setEditMaxPkgs] = useState("");
  const [editDepotId, setEditDepotId] = useState("");
  const [editDepotLat, setEditDepotLat] = useState("");
  const [editDepotLon, setEditDepotLon] = useState("");
  const [editWorkStart, setEditWorkStart] = useState("");
  const [editWorkEnd, setEditWorkEnd] = useState("");
  const [editConstraints, setEditConstraints] = useState<ConstraintSet>({});
  const [constraintsLoading, setConstraintsLoading] = useState(false);
  const [allVehicleTags, setAllVehicleTags] = useState<VehicleTag[]>([]);
  const [createTagIds, setCreateTagIds] = useState<string[]>([]);
  const [editCourierTagIds, setEditCourierTagIds] = useState<string[]>([]);

  // ── Multi-depot vehicle support ──
  // Hidden behind the `multi_depot_vehicles_enabled` tenant feature flag.
  // Empty arrays / empty strings mean "single-depot, current behavior."
  const features = useFeatures();
  const multiDepotEnabled = features.multi_depot_vehicles_enabled;
  const [createAdditionalDepotIds, setCreateAdditionalDepotIds] = useState<string[]>([]);
  const [createEndDepotId, setCreateEndDepotId] = useState<string>("");
  const [editAdditionalDepotIds, setEditAdditionalDepotIds] = useState<string[]>([]);
  const [editEndDepotId, setEditEndDepotId] = useState<string>("");

  function onDepotSelect(selectedDepotId: string, context: "create" | "edit") {
    const depot = depots?.find((candidate) => candidate.id === selectedDepotId);
    if (context === "create") {
      setDepotId(selectedDepotId);
      if (depot) {
        setDepotLat(String(depot.lat));
        setDepotLon(String(depot.lon));
      }
      return;
    }

    setEditDepotId(selectedDepotId);
    if (depot) {
      setEditDepotLat(String(depot.lat));
      setEditDepotLon(String(depot.lon));
    }
  }

  function resetCreate() {
    setUserId("");
    setVehicleType("CAR_VAN");
    setVehicleSpecId("");
    setMaxWeight("50");
    setMaxVolume("");
    setMaxPkgs("");
    setDepotId("");
    setDepotLat("");
    setDepotLon("");
    setWorkStart("08:00");
    setWorkEnd("18:00");
    setCreateTagIds([]);
    setCreateAdditionalDepotIds([]);
    setCreateEndDepotId("");
    setError(null);
  }

  function openEdit(courier: Courier) {
    setShowEdit(courier);
    setEditVehicle(courier.vehicle_type);
    setEditVehicleSpecId(courier.vehicle_spec_id ?? "");
    setEditMaxWeight(String(courier.max_weight_kg));
    setEditMaxVolume(
      courier.max_volume_cm3 != null ? String(courier.max_volume_cm3) : "",
    );
    setEditMaxPkgs(courier.max_packages != null ? String(courier.max_packages) : "");
    setEditDepotId(courier.depot_id ?? "");
    setEditDepotLat(String(courier.depot_lat));
    setEditDepotLon(String(courier.depot_lon));
    setEditWorkStart(courier.working_hours_start ?? "08:00");
    setEditWorkEnd(courier.working_hours_end ?? "18:00");
    setError(null);
    setEditConstraints({});
    setEditCourierTagIds((courier.vehicle_tags ?? []).map((t) => t.id));
    setEditAdditionalDepotIds(courier.additional_depot_ids ?? []);
    setEditEndDepotId(courier.end_depot_id ?? "");
    setConstraintsLoading(true);
    getCourierOverrides(token!, courier.id)
      .then(setEditConstraints)
      .catch(() => undefined)
      .finally(() => setConstraintsLoading(false));
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      const created = await createCourier(token!, {
        user_id: userId,
        vehicle_type: vehicleType,
        ...(vehicleSpecId ? { vehicle_spec_id: vehicleSpecId } : {}),
        max_weight_kg: Number(maxWeight),
        ...(maxVolume ? { max_volume_cm3: Number(maxVolume) } : {}),
        ...(maxPkgs ? { max_packages: Number(maxPkgs) } : {}),
        ...(depotId ? { depot_id: depotId } : {}),
        depot_lat: Number(depotLat),
        depot_lon: Number(depotLon),
        working_hours_start: workStart,
        working_hours_end: workEnd,
        // Multi-depot fields are only sent when the tenant has the feature
        // flag on; otherwise the backend rejects them with 400.
        ...(multiDepotEnabled && createAdditionalDepotIds.length > 0
          ? { additional_depot_ids: createAdditionalDepotIds }
          : {}),
        ...(multiDepotEnabled && createEndDepotId
          ? { end_depot_id: createEndDepotId }
          : {}),
      });
      if (createTagIds.length > 0) {
        await setCourierTags(token!, created.id, createTagIds);
      }
      setShowCreate(false);
      resetCreate();
      addToast("Courier created", "success");
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleEdit(event: FormEvent) {
    event.preventDefault();
    if (!showEdit) {
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const payload: CourierUpdate = {
        vehicle_type: editVehicle,
        vehicle_spec_id: editVehicleSpecId || null,
        max_weight_kg: Number(editMaxWeight),
        ...(editMaxVolume ? { max_volume_cm3: Number(editMaxVolume) } : {}),
        ...(editMaxPkgs ? { max_packages: Number(editMaxPkgs) } : {}),
        ...(editDepotId ? { depot_id: editDepotId } : {}),
        depot_lat: Number(editDepotLat),
        depot_lon: Number(editDepotLon),
        working_hours_start: editWorkStart,
        working_hours_end: editWorkEnd,
        // Multi-depot fields. We always send them when the flag is on so
        // dispatchers can also clear them (empty list / null). When the
        // flag is off, we never include them — the backend would reject.
        ...(multiDepotEnabled
          ? {
              additional_depot_ids: editAdditionalDepotIds,
              end_depot_id: editEndDepotId || null,
            }
          : {}),
      };
      await updateCourier(token!, showEdit.id, payload);

      const constraintPayload: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(editConstraints)) {
        if (value !== null && value !== undefined) {
          constraintPayload[key] = value;
        }
      }

      if (Object.keys(constraintPayload).length > 0) {
        await setCourierConstraints(token!, showEdit.id, constraintPayload as ConstraintSet);
      } else {
        await clearCourierConstraints(token!, showEdit.id);
      }

      await setCourierTags(token!, showEdit.id, editCourierTagIds);

      addToast("Courier updated", "success");
      setShowEdit(null);
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{total} couriers</p>
        <button
          onClick={() => {
            resetCreate();
            setShowCreate(true);
          }}
          className="px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
        >
          + Add Courier
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {isLoading && !couriers ? (
          <div className="p-12 text-center text-gray-400 text-sm">Loading...</div>
        ) : !couriers?.length ? (
          <div className="p-12 text-center text-gray-400 text-sm">No couriers yet</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  ID
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Vehicle
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Spec
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Status
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Max Weight
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Hours
                </th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {couriers.map((courier) => (
                <tr key={courier.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-600">
                    {courier.id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3 text-gray-700">
                    <div className="flex items-center gap-1.5">
                      <span>{VEHICLE_LABELS[courier.vehicle_type]}</span>
                      {multiDepotEnabled &&
                        (courier.additional_depot_ids?.length ?? 0) > 0 && (
                          <span
                            className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-50 text-brand-700"
                            title={`${courier.additional_depot_ids?.length ?? 0} additional reload depot${(courier.additional_depot_ids?.length ?? 0) === 1 ? "" : "s"} allowed`}
                          >
                            Multi-depot
                          </span>
                        )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {courier.vehicle_spec
                      ? `${courier.vehicle_spec.make} ${courier.vehicle_spec.model}${courier.vehicle_spec.year ? ` (${courier.vehicle_spec.year})` : ""}`
                      : <span className="text-gray-400">Default · {vehicleFuelDefaultLabel(courier.vehicle_type)}</span>}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge value={courier.availability_status} />
                  </td>
                  <td className="px-4 py-3 text-gray-600">{courier.max_weight_kg} kg</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {courier.working_hours_start ?? "—"} – {courier.working_hours_end ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => openEdit(courier)}
                      className="text-brand-600 hover:text-brand-700 text-sm font-medium"
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={setPage}
        />
      </div>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Add Courier">
        <form onSubmit={handleCreate} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
            <input
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              placeholder="UUID of user"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Vehicle</label>
              <select
                value={vehicleType}
                onChange={(event) => {
                  setVehicleType(event.target.value as VehicleType);
                  setVehicleSpecId("");
                }}
                className={SETTINGS_INPUT_CLASSNAME}
              >
                {VEHICLE_OPTIONS.map((option) => (
                  <option key={option} value={option}>{VEHICLE_LABELS[option]}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Weight (kg)
              </label>
              <input
                type="number"
                step="0.1"
                value={maxWeight}
                onChange={(event) => setMaxWeight(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Vehicle Spec <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <select
              value={vehicleSpecId}
              onChange={(event) => setVehicleSpecId(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="">Use {VEHICLE_LABELS[vehicleType]} default ({vehicleFuelDefaultLabel(vehicleType)})</option>
              {vehicleSpecs
                .filter((s) => s.vehicle_type === vehicleType)
                .map((s) => (
                  <option key={s.id} value={s.id}>{vehicleSpecLabel(s)}</option>
                ))}
            </select>
            {!vehicleSpecId && (
              <p className="mt-1 text-xs text-gray-400">
                Default fuel profile: {vehicleFuelDefaultLabel(vehicleType)}
              </p>
            )}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Volume (cm³)
              </label>
              <input
                type="number"
                value={maxVolume}
                onChange={(event) => setMaxVolume(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                placeholder="Optional"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Packages
              </label>
              <input
                type="number"
                value={maxPkgs}
                onChange={(event) => setMaxPkgs(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                placeholder="Optional"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Home Depot</label>
            <select
              value={depotId}
              onChange={(event) => onDepotSelect(event.target.value, "create")}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="">— None (manual coords) —</option>
              {depots
                ?.filter((depot) => depot.is_active)
                .map((depot) => (
                  <option key={depot.id} value={depot.id}>
                    {depot.name}
                  </option>
                ))}
            </select>
          </div>
          {multiDepotEnabled && (
            <MultiDepotFields
              depots={depots ?? []}
              homeDepotId={depotId}
              additionalDepotIds={createAdditionalDepotIds}
              onChangeAdditional={setCreateAdditionalDepotIds}
              endDepotId={createEndDepotId}
              onChangeEnd={setCreateEndDepotId}
            />
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Depot Lat</label>
              <input
                type="number"
                step="any"
                value={depotLat}
                onChange={(event) => setDepotLat(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Depot Lon</label>
              <input
                type="number"
                step="any"
                value={depotLon}
                onChange={(event) => setDepotLon(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Work Start
              </label>
              <input
                type="time"
                value={workStart}
                onChange={(event) => setWorkStart(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Work End</label>
              <input
                type="time"
                value={workEnd}
                onChange={(event) => setWorkEnd(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
          </div>
          {allVehicleTags.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Vehicle Tags</label>
              <div className="flex flex-wrap gap-2">
                {allVehicleTags.map((tag) => {
                  const selected = createTagIds.includes(tag.id);
                  return (
                    <button
                      key={tag.id}
                      type="button"
                      onClick={() =>
                        setCreateTagIds((prev) =>
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
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Add Courier"}
            </button>
          </div>
        </form>
      </Modal>

      <Modal open={!!showEdit} onClose={() => setShowEdit(null)} title="Edit Courier">
        <form onSubmit={handleEdit} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Vehicle</label>
              <select
                value={editVehicle}
                onChange={(event) => {
                  setEditVehicle(event.target.value as VehicleType);
                  setEditVehicleSpecId("");
                }}
                className={SETTINGS_INPUT_CLASSNAME}
              >
                {VEHICLE_OPTIONS.map((option) => (
                  <option key={option} value={option}>{VEHICLE_LABELS[option]}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Weight (kg)
              </label>
              <input
                type="number"
                step="0.1"
                value={editMaxWeight}
                onChange={(event) => setEditMaxWeight(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Vehicle Spec <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <select
              value={editVehicleSpecId}
              onChange={(event) => setEditVehicleSpecId(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="">Use {VEHICLE_LABELS[editVehicle]} default ({vehicleFuelDefaultLabel(editVehicle)})</option>
              {vehicleSpecs
                .filter((s) => s.vehicle_type === editVehicle)
                .map((s) => (
                  <option key={s.id} value={s.id}>{vehicleSpecLabel(s)}</option>
                ))}
            </select>
            {!editVehicleSpecId && (
              <p className="mt-1 text-xs text-gray-400">
                Default fuel profile: {vehicleFuelDefaultLabel(editVehicle)}
              </p>
            )}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Volume (cm³)
              </label>
              <input
                type="number"
                value={editMaxVolume}
                onChange={(event) => setEditMaxVolume(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                placeholder="Global default"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Packages
              </label>
              <input
                type="number"
                value={editMaxPkgs}
                onChange={(event) => setEditMaxPkgs(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                placeholder="Global default"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Home Depot</label>
            <select
              value={editDepotId}
              onChange={(event) => onDepotSelect(event.target.value, "edit")}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              <option value="">— None (manual coords) —</option>
              {depots
                ?.filter((depot) => depot.is_active)
                .map((depot) => (
                  <option key={depot.id} value={depot.id}>
                    {depot.name}
                  </option>
                ))}
            </select>
          </div>
          {multiDepotEnabled && (
            <MultiDepotFields
              depots={depots ?? []}
              homeDepotId={editDepotId}
              additionalDepotIds={editAdditionalDepotIds}
              onChangeAdditional={setEditAdditionalDepotIds}
              endDepotId={editEndDepotId}
              onChangeEnd={setEditEndDepotId}
            />
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Depot Lat</label>
              <input
                type="number"
                step="any"
                value={editDepotLat}
                onChange={(event) => setEditDepotLat(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Depot Lon</label>
              <input
                type="number"
                step="any"
                value={editDepotLon}
                onChange={(event) => setEditDepotLon(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Work Start
              </label>
              <input
                type="time"
                value={editWorkStart}
                onChange={(event) => setEditWorkStart(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Work End</label>
              <input
                type="time"
                value={editWorkEnd}
                onChange={(event) => setEditWorkEnd(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
          </div>
          <fieldset className="border border-gray-200 rounded-lg p-4">
            <legend className="text-sm font-medium text-gray-700 px-1">
              Constraint Overrides
            </legend>
            {constraintsLoading ? (
              <p className="text-xs text-gray-400">Loading...</p>
            ) : (
              <div className="space-y-3 mt-1">
                <p className="text-xs text-gray-400">
                  Leave empty to use global defaults
                </p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">
                      Max Distance (km)
                    </label>
                    <input
                      type="number"
                      step="any"
                      value={editConstraints.max_route_distance_km ?? ""}
                      placeholder="Global"
                      onChange={(event) =>
                        setEditConstraints((prev) => ({
                          ...prev,
                          max_route_distance_km: event.target.value
                            ? Number(event.target.value)
                            : null,
                        }))
                      }
                      className={SETTINGS_INPUT_CLASSNAME}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">Max Stops</label>
                    <input
                      type="number"
                      value={editConstraints.max_stops_per_route ?? ""}
                      placeholder="Global"
                      onChange={(event) =>
                        setEditConstraints((prev) => ({
                          ...prev,
                          max_stops_per_route: event.target.value
                            ? Number(event.target.value)
                            : null,
                        }))
                      }
                      className={SETTINGS_INPUT_CLASSNAME}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 text-xs text-gray-600">
                    <input
                      type="checkbox"
                      checked={editConstraints.allow_fragile ?? true}
                      onChange={(event) =>
                        setEditConstraints((prev) => ({
                          ...prev,
                          allow_fragile: event.target.checked,
                        }))
                      }
                    />
                    Allow fragile
                  </label>
                  <label className="flex items-center gap-2 text-xs text-gray-600">
                    <input
                      type="checkbox"
                      checked={editConstraints.allow_temp_sensitive ?? true}
                      onChange={(event) =>
                        setEditConstraints((prev) => ({
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
                  onClick={() => setEditConstraints({})}
                  className="text-xs text-brand-600 hover:text-brand-700"
                >
                  Reset to global
                </button>
              </div>
            )}
          </fieldset>
          {allVehicleTags.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Vehicle Tags</label>
              <div className="flex flex-wrap gap-2">
                {allVehicleTags.map((tag) => {
                  const selected = editCourierTagIds.includes(tag.id);
                  return (
                    <button
                      key={tag.id}
                      type="button"
                      onClick={() =>
                        setEditCourierTagIds((prev) =>
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
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setShowEdit(null)}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}


// ── Multi-depot UI ─────────────────────────────────────────────────────
//
// Rendered only when `features.multi_depot_vehicles_enabled` is on, so the
// container ternary keeps single-depot tenants seeing zero new UI. The
// component itself is unconditional — it assumes the flag is already true.

interface MultiDepotFieldsProps {
  depots: Depot[];
  homeDepotId: string;
  additionalDepotIds: string[];
  onChangeAdditional: (ids: string[]) => void;
  endDepotId: string;
  onChangeEnd: (id: string) => void;
}

function MultiDepotFields({
  depots,
  homeDepotId,
  additionalDepotIds,
  onChangeAdditional,
  endDepotId,
  onChangeEnd,
}: MultiDepotFieldsProps) {
  const activeDepots = useMemo(
    () => depots.filter((d) => d.is_active),
    [depots],
  );
  const additionalCandidates = useMemo(
    () => activeDepots.filter((d) => d.id !== homeDepotId),
    [activeDepots, homeDepotId],
  );
  // End-of-shift candidates = home depot + the currently-selected
  // additional depots. Keeps `end_depot_id ∈ allowed set`, matching the
  // backend validation.
  const endCandidates = useMemo(() => {
    const ids = new Set<string>();
    if (homeDepotId) ids.add(homeDepotId);
    for (const id of additionalDepotIds) ids.add(id);
    return activeDepots.filter((d) => ids.has(d.id));
  }, [activeDepots, homeDepotId, additionalDepotIds]);

  function toggleAdditional(id: string) {
    if (additionalDepotIds.includes(id)) {
      onChangeAdditional(additionalDepotIds.filter((x) => x !== id));
      // If the end depot was the one we just removed, clear it so the
      // user has to pick a still-valid option.
      if (endDepotId === id) onChangeEnd("");
    } else {
      onChangeAdditional([...additionalDepotIds, id]);
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-3 bg-gray-50">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">
          Additional reload depots
        </label>
        {additionalCandidates.length === 0 ? (
          <p className="text-xs text-gray-400">
            No other active depots to choose from.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {additionalCandidates.map((depot) => {
              const selected = additionalDepotIds.includes(depot.id);
              return (
                <button
                  key={depot.id}
                  type="button"
                  onClick={() => toggleAdditional(depot.id)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                    selected
                      ? "bg-brand-600 border-brand-600 text-white"
                      : "border-gray-300 text-gray-600 hover:border-brand-400"
                  }`}
                >
                  {depot.name}
                </button>
              );
            })}
          </div>
        )}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          End-of-shift depot
        </label>
        <select
          value={endDepotId}
          onChange={(event) => onChangeEnd(event.target.value)}
          className={SETTINGS_INPUT_CLASSNAME}
        >
          <option value="">— Same as home depot —</option>
          {endCandidates.map((depot) => (
            <option key={depot.id} value={depot.id}>
              {depot.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
