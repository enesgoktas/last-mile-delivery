import { useEffect, useMemo, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { BatteryCharging, GripVertical, X } from "lucide-react";
import { StatusBadge } from "./ui/Badge";
import { Button } from "./ui/Button";
import type { Courier, Order, Route, RouteStop } from "../types";
import { courierAssignmentLabel } from "../utils/courierDisplay";

function SortableStop({
  stop,
  index,
  selected,
  onSelect,
}: {
  stop: RouteStop;
  index: number;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: stop.id });
  const style = { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 };
  const isDepot = stop.stop_type === "DEPOT_RETURN" || stop.stop_type === "DEPOT_RELOAD";
  const isCharging = stop.stop_type === "CHARGING";
  // Drag is forbidden for any stop the solver inserted (depot rounds + the
  // EV charging stop). The dispatcher can reorder real deliveries around
  // them but the synthetic stops are anchored to the route topology.
  const isLocked = isDepot || isCharging;
  const plannedDurationMin = stop.planned_arrival && stop.planned_departure
    ? Math.max(
        0,
        Math.round(
          (new Date(stop.planned_departure).getTime()
            - new Date(stop.planned_arrival).getTime()) / 60000,
        ),
      )
    : null;

  return (
    <div
      ref={setNodeRef}
      style={style}
      onClick={() => onSelect(stop.id)}
      className={`flex items-center gap-2.5 px-3 py-2 border-b border-slate-100 cursor-pointer transition-colors duration-100 ${
        selected ? "bg-orange-50" : isCharging ? "bg-emerald-50/40" : "hover:bg-slate-50"
      } ${isDepot ? "opacity-50" : ""}`}
    >
      {!isLocked ? (
        <button
          {...attributes}
          {...listeners}
          className="cursor-grab active:cursor-grabbing text-slate-300 hover:text-slate-500 shrink-0"
        >
          <GripVertical size={14} />
        </button>
      ) : (
        <div className="w-3.5 shrink-0" />
      )}

      {isCharging ? (
        <span
          className="w-5 h-5 flex items-center justify-center rounded-full bg-emerald-100 text-emerald-700 shrink-0"
          title="EV charging stop"
        >
          <BatteryCharging size={12} />
        </span>
      ) : (
        <span className="w-5 h-5 flex items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-500 shrink-0 tabular-nums">
          {index + 1}
        </span>
      )}

      <div className="flex-1 min-w-0">
        <p className="text-xs text-slate-800 truncate">{stop.address || stop.stop_type.replace(/_/g, " ")}</p>
        <div className="flex items-center gap-2 mt-0.5">
          {stop.planned_arrival && (
            <span className="text-[10px] text-slate-400 tabular-nums">
              {new Date(stop.planned_arrival).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          {isCharging && plannedDurationMin !== null && plannedDurationMin > 0 && (
            <span className="text-[10px] font-medium text-emerald-700 tabular-nums">
              ~{plannedDurationMin} min charge
            </span>
          )}
          {stop.dispatcher_note && (
            <span className="text-[10px] text-amber-600 truncate">Note</span>
          )}
        </div>
      </div>

      <StatusBadge value={stop.status} />
    </div>
  );
}

const FAILURE_REASON_OPTIONS: { value: string; label: string }[] = [
  { value: "NOT_HOME", label: "Customer not home" },
  { value: "WRONG_ADDRESS", label: "Wrong address" },
  { value: "REFUSED", label: "Refused delivery" },
  { value: "DAMAGED", label: "Damaged item" },
  { value: "ACCESS_DENIED", label: "Access denied" },
  { value: "PASSENGER_NO_SHOW", label: "Passenger no-show (RIDE)" },
  { value: "OTHER", label: "Other" },
];

export function RouteEditorPanel({
  route,
  courier = null,
  pendingOrders = [],
  onReorder,
  onSetDispatcherNote,
  onAssignPendingOrder,
  onSetPreferredCourier,
  onFailStop,
  onClose,
}: {
  route: Route;
  /** Resolved from `route.courier_id` when fleet data is available. */
  courier?: Courier | null;
  pendingOrders?: Order[];
  onReorder: (routeId: string, stopIds: string[]) => Promise<void>;
  onSetDispatcherNote: (routeId: string, stopId: string, note: string | null) => Promise<void>;
  onAssignPendingOrder: (orderId: string, courierId: string) => Promise<void>;
  onSetPreferredCourier: (orderId: string, courierId: string | null) => Promise<void>;
  onFailStop?: (
    routeId: string,
    stopId: string,
    failureReason: string,
    notes: string | null,
  ) => Promise<void>;
  onClose: () => void;
}) {
  const [sortedStops, setSortedStops] = useState<RouteStop[]>(() =>
    [...route.stops].sort((a, b) => a.sequence_number - b.sequence_number),
  );
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [selectedStopId, setSelectedStopId] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [pinningId, setPinningId] = useState<string | null>(null);
  const [failOpen, setFailOpen] = useState(false);
  const [failReason, setFailReason] = useState<string>("NOT_HOME");
  const [failNotes, setFailNotes] = useState("");
  const [failingStop, setFailingStop] = useState(false);

  useEffect(() => {
    setSortedStops([...route.stops].sort((a, b) => a.sequence_number - b.sequence_number));
  }, [route.stops]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const draggableIds = useMemo(() => sortedStops.map((s) => s.id), [sortedStops]);
  const deliveryStops = sortedStops.filter(
    (s) =>
      s.stop_type !== "DEPOT_RETURN" &&
      s.stop_type !== "DEPOT_RELOAD" &&
      s.stop_type !== "CHARGING",
  );
  const selectedStop = useMemo(
    () => sortedStops.find((s) => s.id === selectedStopId) ?? null,
    [selectedStopId, sortedStops],
  );
  const assignableOrders = useMemo(
    () =>
      [...pendingOrders]
        .filter((o) => o.status === "PENDING" && !o.assigned_courier_id)
        .sort((a, b) => Date.parse(a.latest_delivery) - Date.parse(b.latest_delivery))
        .slice(0, 5),
    [pendingOrders],
  );

  useEffect(() => {
    const firstId = deliveryStops[0]?.id ?? null;
    if (!selectedStopId || !sortedStops.some((s) => s.id === selectedStopId)) {
      setSelectedStopId(firstId);
    }
  }, [selectedStopId, sortedStops, deliveryStops]);

  useEffect(() => {
    setNoteDraft(selectedStop?.dispatcher_note ?? "");
    setFailOpen(false);
    setFailReason("NOT_HOME");
    setFailNotes("");
  }, [selectedStop]);

  const noteDirty = (selectedStop?.dispatcher_note ?? "") !== noteDraft;
  const canFailSelectedStop = !!(
    selectedStop
    && onFailStop
    && (selectedStop.status === "PENDING" || selectedStop.status === "ARRIVED")
  );

  async function handleFailStop() {
    if (!selectedStop || !onFailStop) return;
    setFailingStop(true);
    try {
      await onFailStop(
        route.id,
        selectedStop.id,
        failReason,
        failNotes.trim() || null,
      );
      setFailOpen(false);
      setFailNotes("");
    } finally {
      setFailingStop(false);
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const activeStop = sortedStops.find((s) => s.id === active.id);
    const overStop = sortedStops.find((s) => s.id === over.id);
    // CHARGING stops are solver-anchored: moving them invalidates the
    // battery-reachability guarantee the post-processor enforced. Block
    // both source and target so deliveries can't slip under a charger
    // either.
    const lockedTypes = new Set(["DEPOT_RETURN", "DEPOT_RELOAD", "CHARGING"]);
    if (
      (activeStop && lockedTypes.has(activeStop.stop_type)) ||
      (overStop && lockedTypes.has(overStop.stop_type))
    ) return;
    const oldIdx = sortedStops.findIndex((s) => s.id === active.id);
    const newIdx = sortedStops.findIndex((s) => s.id === over.id);
    setSortedStops(arrayMove(sortedStops, oldIdx, newIdx));
    setDirty(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onReorder(route.id, sortedStops.map((s) => s.id));
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveNote() {
    if (!selectedStop) return;
    setSavingNote(true);
    try {
      const trimmed = noteDraft.trim();
      await onSetDispatcherNote(route.id, selectedStop.id, trimmed || null);
      setSortedStops((prev) =>
        prev.map((s) => (s.id === selectedStop.id ? { ...s, dispatcher_note: trimmed || null } : s)),
      );
      setNoteDraft(trimmed);
    } finally {
      setSavingNote(false);
    }
  }

  async function handleAssign(orderId: string) {
    setAssigningId(orderId);
    try {
      await onAssignPendingOrder(orderId, route.courier_id);
    } finally {
      setAssigningId(null);
    }
  }

  async function handlePin(orderId: string, courierId: string | null) {
    setPinningId(orderId);
    try {
      await onSetPreferredCourier(orderId, courierId);
    } finally {
      setPinningId(null);
    }
  }

  return (
    <div className="w-[340px] shrink-0 border-l border-slate-200 bg-white flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-slate-100 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Edit Route</h2>
          <p className="text-xs text-slate-600 mt-0.5">{courierAssignmentLabel(courier, route.courier_id)}</p>
          <p className="text-xs text-slate-400 mt-0.5 tabular-nums">
            Route {route.id.slice(0, 8)} · {deliveryStops.length} stops · {route.planned_distance_km.toFixed(1)} km
          </p>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors duration-150"
        >
          <X size={15} />
        </button>
      </div>

      {/* Stop list */}
      <div className="flex-1 overflow-y-auto thin-scrollbar">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={draggableIds} strategy={verticalListSortingStrategy}>
            {sortedStops.map((stop, idx) => (
              <SortableStop
                key={stop.id}
                stop={stop}
                index={idx}
                selected={selectedStopId === stop.id}
                onSelect={setSelectedStopId}
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>

      {/* Dispatcher note */}
      {selectedStop && (
        <div className="border-t border-slate-100 px-4 py-3 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Note</span>
            <StatusBadge value={selectedStop.status} />
          </div>
          <p className="text-xs text-slate-600 truncate">{selectedStop.address}</p>
          <textarea
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            rows={2}
            placeholder="Dispatcher note…"
            className="w-full border border-slate-200 rounded-md px-2.5 py-2 text-xs text-slate-700 resize-none focus:outline-none focus:border-accent-500"
          />
          <div className="flex items-center justify-between gap-2">
            {canFailSelectedStop && !failOpen ? (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setFailOpen(true)}
                data-testid="open-fail-stop-btn"
              >
                Fail this stop
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => setNoteDraft("")}>Clear</Button>
              <Button variant="secondary" size="sm" disabled={!noteDirty || savingNote} onClick={() => void handleSaveNote()}>
                {savingNote ? "Saving…" : "Save note"}
              </Button>
            </div>
          </div>

          {canFailSelectedStop && failOpen && (
            <div className="mt-2 border border-red-100 bg-red-50/50 rounded-md p-2.5 space-y-2">
              <p className="text-xs font-medium text-red-700">
                Mark this stop failed
              </p>
              <p className="text-[11px] text-slate-600">
                The driver moves to the next stop. For RIDE passenger no-show
                the whole order is cancelled.
              </p>
              <select
                value={failReason}
                onChange={(e) => setFailReason(e.target.value)}
                className="w-full border border-slate-200 rounded-md px-2 py-1.5 text-xs text-slate-800 bg-white focus:outline-none focus:border-red-400"
                data-testid="fail-stop-reason"
              >
                {FAILURE_REASON_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <textarea
                value={failNotes}
                onChange={(e) => setFailNotes(e.target.value)}
                rows={2}
                placeholder="Optional notes for audit log…"
                className="w-full border border-slate-200 rounded-md px-2 py-1.5 text-xs text-slate-700 resize-none bg-white focus:outline-none focus:border-red-400"
              />
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setFailOpen(false);
                    setFailNotes("");
                  }}
                  disabled={failingStop}
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => void handleFailStop()}
                  disabled={failingStop}
                  data-testid="confirm-fail-stop-btn"
                >
                  {failingStop ? "Marking…" : "Mark failed"}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Add pending order */}
      {assignableOrders.length > 0 && (
        <div className="border-t border-slate-100 px-4 py-3 space-y-2 bg-slate-50">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Add order</span>
          {assignableOrders.map((order) => (
            <div key={order.id} className="flex flex-wrap items-center gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-700 truncate">{order.delivery_address.split(",")[0]}</p>
                <p className="text-[10px] text-slate-400 tabular-nums">
                  {order.priority} · {new Date(order.latest_delivery).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
              <Button
                variant={order.preferred_courier_id === route.courier_id ? "secondary" : "ghost"}
                size="sm"
                disabled={pinningId === order.id}
                onClick={() => void handlePin(order.id, order.preferred_courier_id === route.courier_id ? null : route.courier_id)}
              >
                {order.preferred_courier_id === route.courier_id ? "Unpin" : "Pin"}
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={assigningId === order.id}
                onClick={() => void handleAssign(order.id)}
              >
                {assigningId === order.id ? "…" : "Add"}
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Save reorder */}
      {dirty && (
        <div className="border-t border-slate-100 px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 bg-white">
          <span className="text-xs text-amber-600 font-medium">Unsaved order</span>
          <Button variant="primary" size="sm" disabled={saving} onClick={() => void handleSave()}>
            {saving ? "Saving…" : "Save order"}
          </Button>
        </div>
      )}
    </div>
  );
}
