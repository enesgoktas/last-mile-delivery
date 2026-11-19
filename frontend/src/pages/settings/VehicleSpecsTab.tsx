import { useCallback, useEffect, useState } from "react";
import { Search, Plus, Trash2, Fuel, Zap, ChevronRight } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { usePolling } from "../../hooks/usePolling";
import {
  listVehicleSpecs,
  deleteVehicleSpec,
  createVehicleSpec,
  searchEPAYears,
  searchEPAMakes,
  searchEPAModels,
  searchEPA,
  importEPASpec,
  type VehicleSpecCreate,
} from "../../api/client";
import { Modal } from "../../components/Modal";
import type { EPAMenuEntry, EPASearchResult, FuelType, VehicleSpec, VehicleType } from "../../types";
import {
  FUEL_LABELS,
  FUEL_OPTIONS,
  VEHICLE_LABELS,
  VEHICLE_OPTIONS,
  VEHICLE_TYPE_FUEL_DEFAULTS,
} from "../../utils/vehicleFuel";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

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

function SpecRow({
  spec,
  onDelete,
}: {
  spec: VehicleSpec;
  onDelete: (id: string) => void;
}) {
  return (
    <tr className="hover:bg-gray-50/50 group">
      <td className="px-4 py-3 text-gray-800 font-medium text-sm">
        {spec.make} {spec.model}
        {spec.year ? <span className="ml-1 text-gray-400 font-normal text-xs">({spec.year})</span> : null}
      </td>
      <td className="px-4 py-3">
        <span className="text-xs text-gray-600 bg-gray-100 rounded px-1.5 py-0.5">
          {VEHICLE_LABELS[spec.vehicle_type] ?? spec.vehicle_type}
        </span>
      </td>
      <td className="px-4 py-3">
        <FuelBadge fuelType={spec.fuel_type} />
      </td>
      <td className="px-4 py-3 text-sm text-gray-700 tabular-nums">
        {spec.fuel_efficiency_km_per_liter.toFixed(2)} km/L
      </td>
      <td className="px-4 py-3 text-sm text-gray-500 tabular-nums">
        {spec.co2_emissions_g_per_km != null ? `${spec.co2_emissions_g_per_km.toFixed(0)} g/km` : "—"}
      </td>
      <td className="px-4 py-3 text-xs text-gray-400">
        {spec.tenant_id == null ? (
          <span className="bg-slate-100 text-slate-500 rounded px-1.5 py-0.5">Global</span>
        ) : (
          <span className="bg-brand-50 text-brand-600 rounded px-1.5 py-0.5">Custom</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        {spec.tenant_id != null && (
          <button
            onClick={() => onDelete(spec.id)}
            className="text-gray-300 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100"
            title="Delete spec"
          >
            <Trash2 size={14} />
          </button>
        )}
      </td>
    </tr>
  );
}

// ── Manual Create Modal ────────────────────────────────────────────────────

function ManualCreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const { token } = useAuth();
  const { addToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<VehicleSpecCreate>({
    make: "",
    model: "",
    year: undefined,
    vehicle_type: "CAR_VAN",
    fuel_type: "GASOLINE",
    fuel_efficiency_km_per_liter: 10,
    co2_emissions_g_per_km: undefined,
  });

  function set(field: keyof VehicleSpecCreate, value: unknown) {
    setForm((p) => ({ ...p, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await createVehicleSpec(token!, form);
      addToast("Vehicle spec created", "success");
      onCreated();
      onClose();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to create spec", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open title="Add vehicle spec manually" onClose={onClose}>
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Make</label>
            <input
              value={form.make}
              onChange={(e) => set("make", e.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              placeholder="e.g. Toyota"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
            <input
              value={form.model}
              onChange={(e) => set("model", e.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              placeholder="e.g. Corolla"
              required
            />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Year</label>
            <input
              type="number"
              value={form.year ?? ""}
              onChange={(e) => set("year", e.target.value ? Number(e.target.value) : undefined)}
              className={SETTINGS_INPUT_CLASSNAME}
              placeholder="Optional"
              min={1980}
              max={2030}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Vehicle type</label>
            <select
              value={form.vehicle_type}
              onChange={(e) => set("vehicle_type", e.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              {VEHICLE_OPTIONS.map((v) => (
                <option key={v} value={v}>{VEHICLE_LABELS[v]}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Fuel type</label>
            <select
              value={form.fuel_type}
              onChange={(e) => set("fuel_type", e.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
            >
              {FUEL_OPTIONS.map((f) => (
                <option key={f} value={f}>{FUEL_LABELS[f]}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Fuel efficiency (km/L)</label>
            <input
              type="number"
              step="0.01"
              value={form.fuel_efficiency_km_per_liter}
              onChange={(e) => set("fuel_efficiency_km_per_liter", Number(e.target.value))}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              min={0.1}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">CO₂ emissions (g/km)</label>
            <input
              type="number"
              step="1"
              value={form.co2_emissions_g_per_km ?? ""}
              onChange={(e) => set("co2_emissions_g_per_km", e.target.value ? Number(e.target.value) : undefined)}
              className={SETTINGS_INPUT_CLASSNAME}
              placeholder="Optional"
              min={0}
            />
          </div>
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50">
            Cancel
          </button>
          <button type="submit" disabled={saving} className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50">
            {saving ? "Saving…" : "Add spec"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── EPA Import Wizard ──────────────────────────────────────────────────────

type EPAStep = "year" | "make" | "model" | "results";

function EPAImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { token } = useAuth();
  const { addToast } = useToast();

  const [step, setStep] = useState<EPAStep>("year");
  const [years, setYears] = useState<EPAMenuEntry[]>([]);
  const [makes, setMakes] = useState<EPAMenuEntry[]>([]);
  const [models, setModels] = useState<EPAMenuEntry[]>([]);
  const [results, setResults] = useState<EPASearchResult[]>([]);

  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [selectedMake, setSelectedMake] = useState("");
  const [selectedModel, setSelectedModel] = useState("");

  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);

  async function loadYears() {
    setLoading(true);
    try {
      const data = await searchEPAYears(token!);
      setYears(data);
    } catch {
      addToast("Failed to load years from EPA", "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleYearSelect(year: number) {
    setSelectedYear(year);
    setLoading(true);
    try {
      const data = await searchEPAMakes(token!, year);
      setMakes(data);
      setStep("make");
    } catch {
      addToast("Failed to load makes from EPA", "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleMakeSelect(make: string) {
    setSelectedMake(make);
    setLoading(true);
    try {
      const data = await searchEPAModels(token!, selectedYear!, make);
      setModels(data);
      setStep("model");
    } catch {
      addToast("Failed to load models from EPA", "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleModelSelect(model: string) {
    setSelectedModel(model);
    setLoading(true);
    try {
      const data = await searchEPA(token!, { year: selectedYear!, make: selectedMake, model });
      setResults(data);
      setStep("results");
    } catch {
      addToast("Failed to fetch EPA results", "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleImport(epaVehicleId: string) {
    setImporting(epaVehicleId);
    try {
      await importEPASpec(token!, epaVehicleId);
      addToast("Spec imported from EPA", "success");
      onImported();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Import failed", "error");
    } finally {
      setImporting(null);
    }
  }

  // Load years when modal mounts
  useEffect(() => { void loadYears(); }, []);

  const breadcrumb = [
    selectedYear && String(selectedYear),
    selectedMake,
    selectedModel,
  ].filter(Boolean);

  return (
    <Modal open title="Advanced EPA passenger-vehicle import" onClose={onClose} wide>
      <div className="space-y-4">
        {/* Breadcrumb */}
        {breadcrumb.length > 0 && (
          <div className="flex items-center gap-1 text-xs text-gray-500 flex-wrap">
            <button onClick={() => { setStep("year"); setSelectedYear(null); setSelectedMake(""); setSelectedModel(""); }} className="hover:text-brand-600">All years</button>
            {selectedYear && (
              <>
                <ChevronRight size={10} className="text-gray-300" />
                <button onClick={() => { setStep("make"); setSelectedMake(""); setSelectedModel(""); }} className="hover:text-brand-600">{selectedYear}</button>
              </>
            )}
            {selectedMake && (
              <>
                <ChevronRight size={10} className="text-gray-300" />
                <button onClick={() => { setStep("model"); setSelectedModel(""); }} className="hover:text-brand-600">{selectedMake}</button>
              </>
            )}
            {selectedModel && (
              <>
                <ChevronRight size={10} className="text-gray-300" />
                <span className="font-medium text-gray-700">{selectedModel}</span>
              </>
            )}
          </div>
        )}

        {loading && (
          <div className="py-8 text-center text-sm text-gray-400">Loading from EPA…</div>
        )}

        {!loading && step === "year" && (
          <div>
            <p className="text-sm text-gray-500 mb-3">Select a model year:</p>
            <div className="grid grid-cols-5 gap-2 max-h-60 overflow-y-auto thin-scrollbar">
              {years.map((y) => (
                <button
                  key={y.value}
                  onClick={() => void handleYearSelect(Number(y.value))}
                  className="text-sm text-gray-700 border border-gray-200 rounded-lg px-3 py-2 hover:border-brand-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
                >
                  {y.text}
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && step === "make" && (
          <div>
            <p className="text-sm text-gray-500 mb-3">Select a make:</p>
            <div className="grid grid-cols-3 gap-2 max-h-60 overflow-y-auto thin-scrollbar">
              {makes.map((m) => (
                <button
                  key={m.value}
                  onClick={() => void handleMakeSelect(m.value)}
                  className="text-sm text-gray-700 border border-gray-200 rounded-lg px-3 py-2 hover:border-brand-400 hover:text-brand-600 hover:bg-brand-50 transition-colors text-left"
                >
                  {m.text}
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && step === "model" && (
          <div>
            <p className="text-sm text-gray-500 mb-3">Select a model:</p>
            <div className="grid grid-cols-2 gap-2 max-h-60 overflow-y-auto thin-scrollbar">
              {models.map((m) => (
                <button
                  key={m.value}
                  onClick={() => void handleModelSelect(m.value)}
                  className="text-sm text-gray-700 border border-gray-200 rounded-lg px-3 py-2 hover:border-brand-400 hover:text-brand-600 hover:bg-brand-50 transition-colors text-left"
                >
                  {m.text}
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && step === "results" && (
          <div>
            <p className="text-sm text-gray-500 mb-3">{results.length} variant{results.length !== 1 ? "s" : ""} found — select one to import:</p>
            <div className="space-y-2 max-h-80 overflow-y-auto thin-scrollbar">
              {results.map((r) => (
                <div key={r.epa_vehicle_id} className="flex items-center justify-between border border-gray-100 rounded-lg px-4 py-3 hover:border-gray-200 hover:bg-gray-50">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">
                      {r.year} {r.make} {r.model}
                    </p>
                    <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500">
                      <span>{r.fuel_efficiency_km_per_liter.toFixed(2)} km/L</span>
                      {r.co2_emissions_g_per_km != null && (
                        <span>{r.co2_emissions_g_per_km.toFixed(0)} g CO₂/km</span>
                      )}
                      {r.city_mpg != null && r.highway_mpg != null && (
                        <span className="text-gray-400">{r.city_mpg}/{r.highway_mpg} mpg city/hwy</span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => void handleImport(r.epa_vehicle_id)}
                    disabled={importing === r.epa_vehicle_id}
                    className="ml-4 shrink-0 px-3 py-1.5 text-xs font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700 disabled:opacity-50"
                  >
                    {importing === r.epa_vehicle_id ? "Importing…" : "Import"}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex justify-end pt-2 border-t border-gray-100">
          <button onClick={onClose} className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50">
            Close
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Main Tab ───────────────────────────────────────────────────────────────

export function VehicleSpecsTab() {
  const { token } = useAuth();
  const { addToast } = useToast();

  const [showManual, setShowManual] = useState(false);
  const [showEPA, setShowEPA] = useState(false);
  const [filterVehicle, setFilterVehicle] = useState<string>("");
  const [filterFuel, setFilterFuel] = useState<string>("");
  const [search, setSearch] = useState("");

  const fetchSpecs = useCallback(
    () => listVehicleSpecs(token!, { vehicle_type: filterVehicle || undefined, fuel_type: filterFuel || undefined }),
    [token, filterVehicle, filterFuel],
  );
  const { data: specs, isLoading, refetch } = usePolling(fetchSpecs, 60_000);

  const filtered = (specs ?? []).filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      s.make.toLowerCase().includes(q) ||
      s.model.toLowerCase().includes(q) ||
      (s.year != null && String(s.year).includes(q))
    );
  });

  async function handleDelete(id: string) {
    if (!confirm("Remove this vehicle spec? Couriers linked to it will lose their spec assignment.")) return;
    try {
      await deleteVehicleSpec(token!, id);
      addToast("Spec deleted", "success");
      refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to delete", "error");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          {isLoading ? "Loading…" : `${specs?.length ?? 0} specs`}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowManual(true)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-white bg-brand-600 rounded-lg hover:bg-brand-700"
          >
            <Plus size={13} />
            Add manually
          </button>
          <button
            onClick={() => setShowEPA(true)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-600 border border-gray-200 bg-white rounded-lg hover:bg-gray-50 transition-colors"
          >
            <Search size={13} />
            Advanced EPA import
          </button>
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-3">
        {VEHICLE_OPTIONS.map((vehicleType) => {
          const profile = VEHICLE_TYPE_FUEL_DEFAULTS[vehicleType];
          return (
            <div key={vehicleType} className="rounded-lg border border-gray-200 bg-white px-4 py-3">
              <p className="text-xs font-semibold text-gray-900">{VEHICLE_LABELS[vehicleType]}</p>
              <div className="mt-1 flex items-center justify-between gap-3">
                <FuelBadge fuelType={profile.fuel_type} />
                <span className="text-sm tabular-nums text-gray-700">
                  {profile.fuel_efficiency_km_per_liter.toFixed(1)} km/L
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search make or model…"
            className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-brand-400"
          />
        </div>
        <select
          value={filterVehicle}
          onChange={(e) => setFilterVehicle(e.target.value)}
          className="text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-400 text-gray-700"
        >
          <option value="">All vehicles</option>
          {VEHICLE_OPTIONS.map((v) => <option key={v} value={v}>{VEHICLE_LABELS[v]}</option>)}
        </select>
        <select
          value={filterFuel}
          onChange={(e) => setFilterFuel(e.target.value)}
          className="text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-400 text-gray-700"
        >
          <option value="">All fuels</option>
          {FUEL_OPTIONS.map((f) => <option key={f} value={f}>{FUEL_LABELS[f]}</option>)}
        </select>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-gray-400 text-sm">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="p-12 text-center text-gray-400 text-sm">
            {search || filterVehicle || filterFuel ? "No specs match your filters." : (
              <>
                No vehicle specs yet.{" "}
                <button onClick={() => setShowManual(true)} className="text-brand-600 hover:underline">
                  add manually
                </button>.
              </>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Vehicle</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Type</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Fuel</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Efficiency</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">CO₂</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Scope</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filtered.map((spec) => (
                <SpecRow key={spec.id} spec={spec} onDelete={handleDelete} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showManual && (
        <ManualCreateModal onClose={() => setShowManual(false)} onCreated={() => refetch()} />
      )}
      {showEPA && (
        <EPAImportModal onClose={() => setShowEPA(false)} onImported={() => refetch()} />
      )}
    </div>
  );
}
