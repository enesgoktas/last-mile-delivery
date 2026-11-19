import { useCallback, useState, type FormEvent } from "react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { usePolling } from "../../hooks/usePolling";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import {
  createDepot,
  deleteDepot,
  geocodeSearch,
  listDepots,
  updateDepot,
} from "../../api/client";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { StatusBadge } from "../../components/StatusBadge";
import type { Depot, DepotCreate, DepotUpdate } from "../../types";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

type GeocodeResult = {
  display_name: string;
  lat: number;
  lon: number;
};

export function DepotsTab() {
  const { token } = useAuth();
  const { addToast } = useToast();

  const fetchDepots = useCallback(() => listDepots(token!), [token]);
  const { data: depots, isLoading, refetch } = usePolling(fetchDepots, 60_000);

  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState<Depot | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [opStart, setOpStart] = useState("07:00");
  const [opEnd, setOpEnd] = useState("20:00");

  const [editName, setEditName] = useState("");
  const [editAddress, setEditAddress] = useState("");
  const [editLat, setEditLat] = useState("");
  const [editLon, setEditLon] = useState("");
  const [editOpStart, setEditOpStart] = useState("");
  const [editOpEnd, setEditOpEnd] = useState("");
  const [editActive, setEditActive] = useState(true);

  const [geoResults, setGeoResults] = useState<GeocodeResult[]>([]);
  const [geoContext, setGeoContext] = useState<"create" | "edit">("create");

  async function handleGeocode(context: "create" | "edit") {
    const query = context === "create" ? address : editAddress;
    if (!query.trim()) {
      return;
    }

    setGeoContext(context);
    try {
      const results = await geocodeSearch(token!, query);
      setGeoResults(results);
    } catch {
      setGeoResults([]);
    }
  }

  function selectGeoResult(result: GeocodeResult) {
    if (geoContext === "create") {
      setAddress(result.display_name);
      setLat(String(result.lat));
      setLon(String(result.lon));
    } else {
      setEditAddress(result.display_name);
      setEditLat(String(result.lat));
      setEditLon(String(result.lon));
    }
    setGeoResults([]);
  }

  function resetCreate() {
    setName("");
    setAddress("");
    setLat("");
    setLon("");
    setOpStart("07:00");
    setOpEnd("20:00");
    setError(null);
    setGeoResults([]);
  }

  function openEdit(depot: Depot) {
    setShowEdit(depot);
    setEditName(depot.name);
    setEditAddress(depot.address);
    setEditLat(String(depot.lat));
    setEditLon(String(depot.lon));
    setEditOpStart(depot.operating_hours_start);
    setEditOpEnd(depot.operating_hours_end);
    setEditActive(depot.is_active);
    setError(null);
    setGeoResults([]);
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);

    try {
      const payload: DepotCreate = {
        name,
        address,
        lat: Number(lat),
        lon: Number(lon),
        operating_hours_start: opStart,
        operating_hours_end: opEnd,
      };
      await createDepot(token!, payload);
      setShowCreate(false);
      resetCreate();
      addToast("Depot created", "success");
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
      const payload: DepotUpdate = {
        name: editName,
        address: editAddress,
        lat: Number(editLat),
        lon: Number(editLon),
        operating_hours_start: editOpStart,
        operating_hours_end: editOpEnd,
        is_active: editActive,
      };
      await updateDepot(token!, showEdit.id, payload);
      addToast("Depot updated", "success");
      setShowEdit(null);
      refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteDepot(token!, id);
      addToast("Depot deleted", "success");
      refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to delete", "error");
    }
  }

  function renderGeoDropdown(context: "create" | "edit") {
    if (!geoResults.length || geoContext !== context) {
      return null;
    }

    return (
      <div className="absolute z-10 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
        {geoResults.map((result, index) => (
          <button
            key={index}
            type="button"
            onClick={() => selectGeoResult(result)}
            className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 border-b border-gray-100 last:border-0"
          >
            {result.display_name}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{depots?.length ?? 0} depots</p>
        <button
          onClick={() => {
            resetCreate();
            setShowCreate(true);
          }}
          className="px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
        >
          + Add Depot
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {isLoading && !depots ? (
          <LoadingSpinner label="Loading depots…" />
        ) : !depots?.length ? (
          <div className="p-12 text-center text-gray-400 text-sm">No depots configured yet</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Name
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Address
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Hours
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {depots.map((depot) => (
                <tr key={depot.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3 font-medium text-gray-900">{depot.name}</td>
                  <td className="px-4 py-3 text-gray-600 truncate max-w-[300px]">
                    {depot.address}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {depot.operating_hours_start} – {depot.operating_hours_end}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge value={depot.is_active ? "ACTIVE" : "OFFLINE"} />
                  </td>
                  <td className="px-4 py-3 text-right space-x-3">
                    <button
                      onClick={() => openEdit(depot)}
                      className="text-brand-600 hover:text-brand-700 text-sm font-medium"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => setDeleteId(depot.id)}
                      className="text-red-600 hover:text-red-700 text-sm font-medium"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Add Depot">
        <form onSubmit={handleCreate} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              placeholder="Main Warehouse"
            />
          </div>
          <div className="relative">
            <label className="block text-sm font-medium text-gray-700 mb-1">Address</label>
            <div className="flex gap-2">
              <input
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
                placeholder="123 Depot St"
              />
              <button
                type="button"
                onClick={() => handleGeocode("create")}
                className="px-3 py-2 text-xs font-medium text-brand-700 bg-brand-50 border border-brand-200 rounded-lg hover:bg-brand-100 whitespace-nowrap"
              >
                Lookup
              </button>
            </div>
            {renderGeoDropdown("create")}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Latitude</label>
              <input
                type="number"
                step="any"
                value={lat}
                onChange={(event) => setLat(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Longitude</label>
              <input
                type="number"
                step="any"
                value={lon}
                onChange={(event) => setLon(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Opens</label>
              <input
                type="time"
                value={opStart}
                onChange={(event) => setOpStart(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Closes</label>
              <input
                type="time"
                value={opEnd}
                onChange={(event) => setOpEnd(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
          </div>
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
              {saving ? "Saving..." : "Add Depot"}
            </button>
          </div>
        </form>
      </Modal>

      <Modal open={!!showEdit} onClose={() => setShowEdit(null)} title="Edit Depot">
        <form onSubmit={handleEdit} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              value={editName}
              onChange={(event) => setEditName(event.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
            />
          </div>
          <div className="relative">
            <label className="block text-sm font-medium text-gray-700 mb-1">Address</label>
            <div className="flex gap-2">
              <input
                value={editAddress}
                onChange={(event) => setEditAddress(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
              <button
                type="button"
                onClick={() => handleGeocode("edit")}
                className="px-3 py-2 text-xs font-medium text-brand-700 bg-brand-50 border border-brand-200 rounded-lg hover:bg-brand-100 whitespace-nowrap"
              >
                Lookup
              </button>
            </div>
            {renderGeoDropdown("edit")}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Latitude</label>
              <input
                type="number"
                step="any"
                value={editLat}
                onChange={(event) => setEditLat(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Longitude</label>
              <input
                type="number"
                step="any"
                value={editLon}
                onChange={(event) => setEditLon(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Opens</label>
              <input
                type="time"
                value={editOpStart}
                onChange={(event) => setEditOpStart(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Closes</label>
              <input
                type="time"
                value={editOpEnd}
                onChange={(event) => setEditOpEnd(event.target.value)}
                className={SETTINGS_INPUT_CLASSNAME}
              />
            </div>
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={editActive}
                onChange={(event) => setEditActive(event.target.checked)}
              />
              Active
            </label>
          </div>
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

      <ConfirmDialog
        open={!!deleteId}
        onCancel={() => setDeleteId(null)}
        onConfirm={() => {
          if (deleteId) {
            handleDelete(deleteId);
            setDeleteId(null);
          }
        }}
        title="Delete depot?"
        message="This will unlink any couriers and orders associated with this depot."
        confirmLabel="Delete"
        danger
      />
    </div>
  );
}
