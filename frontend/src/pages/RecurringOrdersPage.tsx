import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Pause, Play, Trash2, RefreshCw, Calendar } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useFeatures } from "../hooks/useFeatures";
import {
  createRecurringTemplate,
  deleteRecurringTemplate,
  listRecurringTemplates,
  previewRecurringTemplate,
  runAllRecurringTemplates,
  runRecurringTemplate,
  updateRecurringTemplate,
  listDepots,
  listVehicleTags,
} from "../api/client";
import { Button } from "../components/ui/Button";
import { Modal } from "../components/Modal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { AddressAutocomplete } from "../components/AddressAutocomplete";
import type {
  Depot,
  OrderPriority,
  OrderType,
  RecurringOrderTemplate,
  RecurringOrderTemplateCreate,
  RecurrenceCadence,
  VehicleTag,
} from "../types";

const CADENCES: { id: RecurrenceCadence; label: string; helper: string }[] = [
  { id: "DAILY", label: "Every day", helper: "Fires every calendar day" },
  { id: "WEEKLY", label: "Weekly", helper: "Pick the days of the week below" },
  { id: "BIWEEKLY", label: "Every 2 weeks", helper: "Anchored to start date, every 14 days" },
  { id: "MONTHLY", label: "Monthly", helper: "On the same date each month (capped to month length)" },
  { id: "CUSTOM", label: "Custom interval", helper: "Every N days from the start date" },
];

const DELIVERY_ORDER_TYPES: OrderType[] = [
  "SCHEDULED",
  "BULK",
  "PICKUP_AND_DELIVERY",
  "RETURN",
  "REVERSE",
  "ON_DEMAND",
];

const PRIORITIES: OrderPriority[] = ["LOW", "NORMAL", "HIGH", "URGENT"];

const WEEKDAYS = [
  { idx: 0, label: "Mon" },
  { idx: 1, label: "Tue" },
  { idx: 2, label: "Wed" },
  { idx: 3, label: "Thu" },
  { idx: 4, label: "Fri" },
  { idx: 5, label: "Sat" },
  { idx: 6, label: "Sun" },
];

const todayISO = () => new Date().toISOString().slice(0, 10);

const emptyTemplate: RecurringOrderTemplateCreate = {
  name: "",
  description: "",
  is_active: true,
  cadence: "WEEKLY",
  interval_days: 1,
  day_of_week_mask: 0b0010101, // Mon, Wed, Fri
  day_of_month: null,
  start_date: todayISO(),
  end_date: null,
  max_occurrences: null,
  delivery_window_start: "09:00:00",
  delivery_window_end: "11:00:00",
  pickup_window_start: null,
  pickup_window_end: null,
  lead_time_days: 7,
  order_type: "SCHEDULED",
  priority: "NORMAL",
  pickup_address: "",
  pickup_lat: 0,
  pickup_lon: 0,
  delivery_address: "",
  delivery_lat: 0,
  delivery_lon: 0,
  depot_id: null,
  weight_kg: 1,
  is_fragile: false,
  is_temp_sensitive: false,
  customer_email: "",
  customer_phone: "",
  external_ref_prefix: "",
  custom_fields: {},
  notes: "",
  passenger_count: null,
  passenger_name: null,
  passenger_phone: null,
  flight_info: null,
  special_requirements: null,
  pickup_buffer_minutes_override: null,
  allow_split: false,
  passengers_per_vehicle: null,
};

function maskToString(mask: number) {
  const days = WEEKDAYS.filter((w) => mask & (1 << w.idx)).map((w) => w.label);
  return days.length ? days.join(", ") : "auto (start day)";
}

function fmtCadence(t: RecurringOrderTemplate) {
  switch (t.cadence) {
    case "DAILY":
      return "Every day";
    case "WEEKLY":
      return `Weekly · ${maskToString(t.day_of_week_mask)}`;
    case "BIWEEKLY":
      return "Every 2 weeks";
    case "MONTHLY":
      return `Monthly · day ${t.day_of_month ?? "(start)"}`;
    case "CUSTOM":
      return `Every ${t.interval_days} days`;
    default:
      return t.cadence;
  }
}

function fmtDate(value: string | null | undefined) {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function RecurringOrdersPage() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const features = useFeatures();
  const orderTypes = useMemo<OrderType[]>(
    () =>
      features.rides_enabled
        ? [...DELIVERY_ORDER_TYPES, "RIDE"]
        : DELIVERY_ORDER_TYPES,
    [features.rides_enabled],
  );
  const [templates, setTemplates] = useState<RecurringOrderTemplate[]>([]);
  const [depots, setDepots] = useState<Depot[]>([]);
  const [vehicleTags, setVehicleTags] = useState<VehicleTag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<RecurringOrderTemplate | null>(null);
  const [form, setForm] = useState<RecurringOrderTemplateCreate>(emptyTemplate);
  const [previewing, setPreviewing] = useState<RecurringOrderTemplate | null>(null);
  const [previewDates, setPreviewDates] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<RecurringOrderTemplate | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadTemplates = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [list, depotList, tagList] = await Promise.all([
        listRecurringTemplates(token),
        listDepots(token).catch(() => []),
        listVehicleTags(token).catch(() => []),
      ]);
      setTemplates(list);
      setDepots(depotList ?? []);
      setVehicleTags(tagList ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load recurring templates");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyTemplate });
    setShowCreate(true);
  };

  const openEdit = (t: RecurringOrderTemplate) => {
    setEditing(t);
    setForm({
      name: t.name,
      description: t.description ?? "",
      is_active: t.is_active,
      cadence: t.cadence,
      interval_days: t.interval_days,
      day_of_week_mask: t.day_of_week_mask,
      day_of_month: t.day_of_month ?? null,
      start_date: t.start_date.slice(0, 10),
      end_date: t.end_date ? t.end_date.slice(0, 10) : null,
      max_occurrences: t.max_occurrences ?? null,
      delivery_window_start: t.delivery_window_start,
      delivery_window_end: t.delivery_window_end,
      pickup_window_start: t.pickup_window_start ?? null,
      pickup_window_end: t.pickup_window_end ?? null,
      lead_time_days: t.lead_time_days,
      order_type: t.order_type,
      priority: t.priority,
      pickup_address: t.pickup_address,
      pickup_lat: t.pickup_lat,
      pickup_lon: t.pickup_lon,
      delivery_address: t.delivery_address,
      delivery_lat: t.delivery_lat,
      delivery_lon: t.delivery_lon,
      depot_id: t.depot_id ?? null,
      weight_kg: t.weight_kg,
      is_fragile: t.is_fragile,
      is_temp_sensitive: t.is_temp_sensitive,
      length_cm: t.length_cm ?? null,
      width_cm: t.width_cm ?? null,
      height_cm: t.height_cm ?? null,
      volume_cm3: t.volume_cm3 ?? null,
      customer_email: t.customer_email,
      customer_phone: t.customer_phone ?? "",
      external_ref_prefix: t.external_ref_prefix ?? "",
      custom_fields: t.custom_fields ?? {},
      notes: t.notes ?? "",
      required_vehicle_tag_ids: (t.required_vehicle_tags ?? []).map((tag) => tag.id),
      passenger_count: t.passenger_count ?? null,
      passenger_name: t.passenger_name ?? null,
      passenger_phone: t.passenger_phone ?? null,
      flight_info: t.flight_info ?? null,
      special_requirements: t.special_requirements ?? null,
      pickup_buffer_minutes_override: t.pickup_buffer_minutes_override ?? null,
      allow_split: t.allow_split ?? false,
      passengers_per_vehicle: t.passengers_per_vehicle ?? null,
    });
    setShowCreate(true);
  };

  const togglePause = async (t: RecurringOrderTemplate) => {
    if (!token) return;
    try {
      await updateRecurringTemplate(token, t.id, { is_active: !t.is_active });
      addToast(t.is_active ? "Template paused" : "Template resumed", "success");
      void loadTemplates();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to update template", "error");
    }
  };

  const runOne = async (t: RecurringOrderTemplate) => {
    if (!token) return;
    try {
      const result = await runRecurringTemplate(token, t.id);
      addToast(
        `${result.occurrences_created} order${result.occurrences_created === 1 ? "" : "s"} generated`,
        "success",
      );
      void loadTemplates();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Run failed", "error");
    }
  };

  const runAll = async () => {
    if (!token) return;
    try {
      const result = await runAllRecurringTemplates(token);
      addToast(
        `Materialised ${result.occurrences_created} order${result.occurrences_created === 1 ? "" : "s"} ` +
          `from ${result.templates_evaluated} active template${result.templates_evaluated === 1 ? "" : "s"}`,
        "success",
      );
      void loadTemplates();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Bulk run failed", "error");
    }
  };

  const openPreview = async (t: RecurringOrderTemplate) => {
    if (!token) return;
    try {
      const result = await previewRecurringTemplate(token, t.id, 60);
      setPreviewing(t);
      setPreviewDates(result.occurrence_dates);
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Preview failed", "error");
    }
  };

  const submit = async () => {
    if (!token) return;
    if (!form.name.trim()) {
      addToast("Name is required", "error");
      return;
    }
    if (!form.pickup_address.trim() || !form.delivery_address.trim()) {
      addToast("Pickup and delivery addresses are required", "error");
      return;
    }
    if (!form.customer_email.trim()) {
      addToast("Customer email is required", "error");
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await updateRecurringTemplate(token, editing.id, form);
        addToast("Template updated", "success");
      } else {
        await createRecurringTemplate(token, form);
        addToast("Template created", "success");
      }
      setShowCreate(false);
      setEditing(null);
      void loadTemplates();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Save failed", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!token || !confirmDelete) return;
    try {
      await deleteRecurringTemplate(token, confirmDelete.id);
      addToast("Template deleted", "success");
      setConfirmDelete(null);
      void loadTemplates();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Delete failed", "error");
    }
  };

  const sortedTemplates = useMemo(
    () =>
      [...templates].sort((a, b) => {
        if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [templates],
  );

  const setMaskBit = (idx: number, value: boolean) => {
    const bit = 1 << idx;
    const current = form.day_of_week_mask ?? 0;
    setForm({
      ...form,
      day_of_week_mask: value ? current | bit : current & ~bit,
    });
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Recurring orders</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Templates that generate orders on a cadence — daily, weekly, monthly, or custom
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void runAll()}
            disabled={loading || templates.filter((t) => t.is_active).length === 0}
            title="Run every active template now"
          >
            <RefreshCw size={13} />
            Materialise now
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={openCreate}
            data-testid="recurring-new-btn"
          >
            <Plus size={13} />
            New template
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 thin-scrollbar">
        {loading ? (
          <div className="py-16 text-center text-sm text-slate-400">Loading…</div>
        ) : error ? (
          <div className="py-16 text-center">
            <p className="text-sm text-red-600">{error}</p>
            <button
              onClick={() => void loadTemplates()}
              className="mt-2 text-xs text-accent-600 hover:underline"
            >
              Try again
            </button>
          </div>
        ) : sortedTemplates.length === 0 ? (
          <div className="py-16 text-center text-sm text-slate-400">
            No recurring templates yet. Click <strong>New template</strong> to create one.
          </div>
        ) : (
          <table data-testid="recurring-templates-table" className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Name
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Cadence
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Window
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Generated
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Status
                </th>
                <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedTemplates.map((t) => (
                <tr
                  key={t.id}
                  data-testid="recurring-template-row"
                  className="border-b border-slate-100 hover:bg-slate-50"
                >
                  <td className="py-3 px-3">
                    <button
                      onClick={() => void openPreview(t)}
                      className="font-medium text-slate-900 hover:text-accent-600"
                    >
                      {t.name}
                    </button>
                    {t.description && (
                      <div className="text-xs text-slate-400 mt-0.5">{t.description}</div>
                    )}
                  </td>
                  <td className="py-3 px-3 text-slate-700">{fmtCadence(t)}</td>
                  <td className="py-3 px-3 text-slate-600">
                    {t.delivery_window_start.slice(0, 5)}–{t.delivery_window_end.slice(0, 5)}
                  </td>
                  <td className="py-3 px-3 text-slate-600 tabular-nums">
                    {t.occurrences_generated}
                    {t.max_occurrences !== null && t.max_occurrences !== undefined
                      ? ` / ${t.max_occurrences}`
                      : ""}
                  </td>
                  <td className="py-3 px-3">
                    <span
                      className={
                        t.is_active
                          ? "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700"
                          : "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600"
                      }
                    >
                      {t.is_active ? "Active" : "Paused"}
                    </span>
                  </td>
                  <td className="py-3 px-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => void openPreview(t)}
                        title="Preview upcoming dates"
                        className="p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
                      >
                        <Calendar size={13} />
                      </button>
                      <button
                        onClick={() => void runOne(t)}
                        title="Run now"
                        disabled={!t.is_active}
                        className="p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <RefreshCw size={13} />
                      </button>
                      <button
                        onClick={() => void togglePause(t)}
                        title={t.is_active ? "Pause" : "Resume"}
                        className="p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
                      >
                        {t.is_active ? <Pause size={13} /> : <Play size={13} />}
                      </button>
                      <button
                        onClick={() => openEdit(t)}
                        className="px-2 py-1 text-xs text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setConfirmDelete(t)}
                        title="Delete"
                        className="p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create / Edit modal */}
      <Modal
        open={showCreate}
        onClose={() => {
          setShowCreate(false);
          setEditing(null);
        }}
        title={editing ? `Edit template — ${editing.name}` : "New recurring template"}
        wide
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs font-medium text-slate-600">Name</span>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Acme HQ weekly drop"
                className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:border-accent-500"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-600">Order type</span>
              <select
                value={form.order_type}
                onChange={(e) => {
                  const next = e.target.value as OrderType;
                  setForm((prev) => {
                    const switchingToRide =
                      next === "RIDE" && prev.order_type !== "RIDE";
                    const switchingFromRide =
                      next !== "RIDE" && prev.order_type === "RIDE";
                    return {
                      ...prev,
                      order_type: next,
                      // RIDE templates need a pickup ready window and at
                      // least one passenger; pre-fill so the form lands
                      // valid and the schema-side validator doesn't fire
                      // on the first save.
                      passenger_count: switchingToRide
                        ? prev.passenger_count ?? 1
                        : switchingFromRide
                          ? null
                          : prev.passenger_count,
                      pickup_window_start: switchingToRide
                        ? prev.pickup_window_start ?? "08:00:00"
                        : prev.pickup_window_start,
                      pickup_window_end: switchingToRide
                        ? prev.pickup_window_end ?? "10:00:00"
                        : prev.pickup_window_end,
                      // RIDE templates have no package — clear the
                      // delivery-only fields when toggling on.
                      weight_kg: switchingToRide
                        ? null
                        : switchingFromRide
                          ? prev.weight_kg ?? 1
                          : prev.weight_kg,
                      is_fragile: switchingToRide ? false : prev.is_fragile,
                      is_temp_sensitive: switchingToRide
                        ? false
                        : prev.is_temp_sensitive,
                      passenger_name: switchingFromRide ? null : prev.passenger_name,
                      passenger_phone: switchingFromRide ? null : prev.passenger_phone,
                      flight_info: switchingFromRide ? null : prev.flight_info,
                      special_requirements: switchingFromRide
                        ? null
                        : prev.special_requirements,
                      pickup_buffer_minutes_override: switchingFromRide
                        ? null
                        : prev.pickup_buffer_minutes_override,
                      allow_split: switchingFromRide ? false : prev.allow_split,
                      passengers_per_vehicle: switchingFromRide
                        ? null
                        : prev.passengers_per_vehicle,
                    };
                  });
                }}
                className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
              >
                {orderTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block">
            <span className="text-xs font-medium text-slate-600">Description (optional)</span>
            <input
              value={form.description ?? ""}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What is this template for?"
              className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
            />
          </label>

          <div className="border-t border-slate-100 pt-3 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Cadence</span>
                <select
                  value={form.cadence}
                  onChange={(e) =>
                    setForm({ ...form, cadence: e.target.value as RecurrenceCadence })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  data-testid="recurring-cadence-select"
                >
                  {CADENCES.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <p className="text-[11px] text-slate-400 mt-1">
                  {CADENCES.find((c) => c.id === form.cadence)?.helper}
                </p>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Priority</span>
                <select
                  value={form.priority}
                  onChange={(e) =>
                    setForm({ ...form, priority: e.target.value as OrderPriority })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {form.cadence === "WEEKLY" && (
              <div>
                <span className="text-xs font-medium text-slate-600">Days of week</span>
                <div className="mt-1 flex gap-1">
                  {WEEKDAYS.map((w) => {
                    const active = !!((form.day_of_week_mask ?? 0) & (1 << w.idx));
                    return (
                      <button
                        type="button"
                        key={w.idx}
                        onClick={() => setMaskBit(w.idx, !active)}
                        className={`px-3 py-1.5 text-xs rounded-md border ${
                          active
                            ? "bg-accent-500 text-white border-accent-500"
                            : "bg-white text-slate-700 border-slate-200 hover:border-slate-400"
                        }`}
                      >
                        {w.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {form.cadence === "MONTHLY" && (
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Day of month</span>
                <input
                  type="number"
                  min={1}
                  max={31}
                  value={form.day_of_month ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      day_of_month: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  className="mt-1 w-32 text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  Falls back to the day of the month from the start date if blank
                </p>
              </label>
            )}

            {form.cadence === "CUSTOM" && (
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Every N days</span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={form.interval_days ?? 1}
                  onChange={(e) =>
                    setForm({ ...form, interval_days: Number(e.target.value) })
                  }
                  className="mt-1 w-32 text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
            )}

            <div className="grid grid-cols-3 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Start date</span>
                <input
                  type="date"
                  value={form.start_date}
                  onChange={(e) => setForm({ ...form, start_date: e.target.value })}
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">End date (optional)</span>
                <input
                  type="date"
                  value={form.end_date ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, end_date: e.target.value || null })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Max occurrences</span>
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={form.max_occurrences ?? ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      max_occurrences: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  placeholder="Unlimited"
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Delivery window start</span>
                <input
                  type="time"
                  step={60}
                  value={form.delivery_window_start.slice(0, 5)}
                  onChange={(e) =>
                    setForm({ ...form, delivery_window_start: `${e.target.value}:00` })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Delivery window end</span>
                <input
                  type="time"
                  step={60}
                  value={form.delivery_window_end.slice(0, 5)}
                  onChange={(e) =>
                    setForm({ ...form, delivery_window_end: `${e.target.value}:00` })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Lead time (days)</span>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={form.lead_time_days ?? 7}
                  onChange={(e) =>
                    setForm({ ...form, lead_time_days: Number(e.target.value) })
                  }
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
            </div>
          </div>

          <div className="border-t border-slate-100 pt-3 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Order template
            </h3>

            <div className="grid grid-cols-2 gap-3">
              <AddressAutocomplete
                label="Pickup address"
                value={form.pickup_address}
                onSelect={(result) =>
                  setForm({
                    ...form,
                    pickup_address: result.display_name,
                    pickup_lat: result.lat,
                    pickup_lon: result.lon,
                  })
                }
                placeholder="Where pickup happens"
              />
              <AddressAutocomplete
                label="Delivery address"
                value={form.delivery_address}
                onSelect={(result) =>
                  setForm({
                    ...form,
                    delivery_address: result.display_name,
                    delivery_lat: result.lat,
                    delivery_lon: result.lon,
                  })
                }
                placeholder="Where delivery happens"
              />
            </div>

            <div className="grid grid-cols-3 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Warehouse (optional)</span>
                <select
                  value={form.depot_id ?? ""}
                  onChange={(e) => setForm({ ...form, depot_id: e.target.value || null })}
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                >
                  <option value="">— None —</option>
                  {depots.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              {form.order_type === "RIDE" ? (
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Passengers</span>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={form.passenger_count ?? 1}
                    onChange={(e) =>
                      setForm({ ...form, passenger_count: Number(e.target.value) })
                    }
                    className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  />
                </label>
              ) : (
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Weight (kg)</span>
                  <input
                    type="number"
                    min={0}
                    step={0.1}
                    value={form.weight_kg ?? 0}
                    onChange={(e) => setForm({ ...form, weight_kg: Number(e.target.value) })}
                    className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  />
                </label>
              )}
              <label className="block">
                <span className="text-xs font-medium text-slate-600">External ref prefix</span>
                <input
                  value={form.external_ref_prefix ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, external_ref_prefix: e.target.value })
                  }
                  placeholder="e.g. ACME-WK"
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Customer email</span>
                <input
                  type="email"
                  value={form.customer_email}
                  onChange={(e) => setForm({ ...form, customer_email: e.target.value })}
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Customer phone</span>
                <input
                  value={form.customer_phone ?? ""}
                  onChange={(e) => setForm({ ...form, customer_phone: e.target.value })}
                  className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                />
              </label>
            </div>

            {(form.order_type === "PICKUP_AND_DELIVERY" ||
              form.pickup_window_start !== null ||
              form.pickup_window_end !== null) && (
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Pickup window start</span>
                  <input
                    type="time"
                    step={60}
                    value={(form.pickup_window_start ?? "08:00:00").slice(0, 5)}
                    onChange={(e) =>
                      setForm({ ...form, pickup_window_start: `${e.target.value}:00` })
                    }
                    className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Pickup window end</span>
                  <input
                    type="time"
                    step={60}
                    value={(form.pickup_window_end ?? "10:00:00").slice(0, 5)}
                    onChange={(e) =>
                      setForm({ ...form, pickup_window_end: `${e.target.value}:00` })
                    }
                    className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  />
                </label>
              </div>
            )}

            <div className="flex gap-4">
              {form.order_type !== "RIDE" && (
                <>
                  <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.is_fragile ?? false}
                      onChange={(e) => setForm({ ...form, is_fragile: e.target.checked })}
                    />
                    Fragile
                  </label>
                  <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.is_temp_sensitive ?? false}
                      onChange={(e) =>
                        setForm({ ...form, is_temp_sensitive: e.target.checked })
                      }
                    />
                    Temperature-sensitive
                  </label>
                </>
              )}
              <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={form.is_active ?? true}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                />
                Active
              </label>
            </div>

            {form.order_type === "RIDE" && (
              <div className="border-t border-slate-100 pt-3 space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Ride details
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">Passenger name (optional)</span>
                    <input
                      value={form.passenger_name ?? ""}
                      onChange={(e) =>
                        setForm({ ...form, passenger_name: e.target.value || null })
                      }
                      placeholder="e.g. Mr. Yılmaz"
                      className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">Passenger phone (optional)</span>
                    <input
                      value={form.passenger_phone ?? ""}
                      onChange={(e) =>
                        setForm({ ...form, passenger_phone: e.target.value || null })
                      }
                      className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                    />
                  </label>
                </div>
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Special requirements (optional)</span>
                  <textarea
                    value={form.special_requirements ?? ""}
                    onChange={(e) =>
                      setForm({ ...form, special_requirements: e.target.value || null })
                    }
                    placeholder="e.g. wheelchair-accessible vehicle"
                    rows={2}
                    className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                  />
                </label>
                {features.ride_pickup_buffer_enabled && (
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">
                      Pickup buffer override (minutes)
                    </span>
                    <input
                      type="number"
                      min={0}
                      value={form.pickup_buffer_minutes_override ?? ""}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          pickup_buffer_minutes_override: e.target.value
                            ? Number(e.target.value)
                            : null,
                        })
                      }
                      placeholder="Falls back to tenant default"
                      className="mt-1 w-48 text-sm border border-slate-200 rounded-md px-2 py-1.5"
                    />
                  </label>
                )}
                {(form.passenger_count ?? 0) > 4 && !form.allow_split && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    This recurring ride has {form.passenger_count} passengers — consider splitting it
                    across multiple vehicles.
                  </div>
                )}
                <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={form.allow_split ?? false}
                    onChange={(e) => setForm({ ...form, allow_split: e.target.checked })}
                  />
                  Allow this ride to be split across multiple vehicles
                </label>
                {form.allow_split && (
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">Passengers per vehicle</span>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={form.passengers_per_vehicle ?? ""}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          passengers_per_vehicle: e.target.value
                            ? Number(e.target.value)
                            : null,
                        })
                      }
                      placeholder="Falls back to tenant default"
                      className="mt-1 w-48 text-sm border border-slate-200 rounded-md px-2 py-1.5"
                    />
                  </label>
                )}
              </div>
            )}
            {vehicleTags.length > 0 && (
              <div className="mt-3">
                <p className="text-sm font-medium text-slate-700 mb-2">Required vehicle tags</p>
                <div className="flex flex-wrap gap-2">
                  {vehicleTags.map((tag) => {
                    const selected = (form.required_vehicle_tag_ids ?? []).includes(tag.id);
                    return (
                      <button
                        key={tag.id}
                        type="button"
                        onClick={() => {
                          const current = form.required_vehicle_tag_ids ?? [];
                          setForm({
                            ...form,
                            required_vehicle_tag_ids: selected
                              ? current.filter((id) => id !== tag.id)
                              : [...current, tag.id],
                          });
                        }}
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
          </div>

          <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setShowCreate(false);
                setEditing(null);
              }}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void submit()}
              disabled={submitting}
              data-testid="recurring-save-btn"
            >
              {submitting ? "Saving…" : editing ? "Save changes" : "Create template"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Preview modal */}
      <Modal
        open={previewing !== null}
        onClose={() => setPreviewing(null)}
        title={previewing ? `Upcoming dates — ${previewing.name}` : ""}
      >
        {previewing && (
          <div>
            <p className="text-sm text-slate-600 mb-3">
              The next 60 days will produce {previewDates.length} order
              {previewDates.length === 1 ? "" : "s"}.
            </p>
            {previewDates.length === 0 ? (
              <p className="text-sm text-slate-400">No occurrences in this window.</p>
            ) : (
              <ul className="grid grid-cols-2 gap-1 text-sm text-slate-700">
                {previewDates.map((d) => (
                  <li key={d} className="px-2 py-1 rounded bg-slate-50">
                    {fmtDate(d)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete recurring template?"
        message={
          confirmDelete
            ? `"${confirmDelete.name}" will stop generating orders. Existing orders are kept.`
            : ""
        }
        confirmLabel="Delete"
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(null)}
        danger
      />
    </div>
  );
}
