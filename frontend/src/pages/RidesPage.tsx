import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BarChart2,
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  Plane,
  Plus,
  RotateCcw,
  Search,
  Zap,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../state/AuthContext";
import { useAssign } from "../state/AssignContext";
import { useFeatures } from "../hooks/useFeatures";
import { usePolling } from "../hooks/usePolling";
import {
  cancelOrder,
  createOrder,
  dispatchOrderNow,
  getOrder,
  listOrders,
  listOrdersPaginated,
  listVehicleTags,
  rescheduleOrder,
  triggerUnassign,
} from "../api/client";
import type { GeocodeResult } from "../api/client";
import { AddressAutocomplete } from "../components/AddressAutocomplete";
import { Button } from "../components/ui/Button";
import { StatusBadge } from "../components/StatusBadge";
import { SlideOver } from "../components/ui/SlideOver";
import { Tabs } from "../components/ui/Tabs";
import { Modal } from "../components/Modal";
import { Pagination } from "../components/Pagination";
import { canRunDispatcherActions } from "../config/roles";
import { useToast } from "../state/ToastContext";
import type { Order, OrderCreate, VehicleTag } from "../types";

const POLL = 30_000;
const PAGE_SIZE = 25;

const STATUS_TABS: { id: string; label: string }[] = [
  { id: "", label: "All" },
  { id: "PENDING", label: "Pending" },
  { id: "ASSIGNED", label: "Assigned" },
  { id: "PICKED_UP", label: "Picked up" },
  { id: "IN_TRANSIT", label: "In transit" },
  { id: "DELIVERED", label: "Completed" },
  { id: "CANCELLED", label: "Cancelled" },
];

const inputCls =
  "w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-800 focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20";
const labelCls = "block text-xs font-medium text-slate-700 mb-1";

function todayDateStr() {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between py-2 border-b border-slate-50 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-xs font-medium text-slate-900 text-right max-w-[60%]">
        {value}
      </span>
    </div>
  );
}

const RIDE_INITIAL: RideFormState = {
  pickup_address: "",
  pickup_lat: "",
  pickup_lon: "",
  delivery_address: "",
  delivery_lat: "",
  delivery_lon: "",
  passenger_count: "1",
  passenger_name: "",
  passenger_phone: "",
  earliest_pickup: "",
  latest_pickup: "",
  pickup_buffer_minutes: "",
  flight_number: "",
  terminal: "",
  customer_email: "",
  special_requirements: "",
  required_vehicle_tag_ids: [],
  allow_split: false,
  passengers_per_vehicle: "",
};

interface RideFormState {
  pickup_address: string;
  pickup_lat: string;
  pickup_lon: string;
  delivery_address: string;
  delivery_lat: string;
  delivery_lon: string;
  passenger_count: string;
  passenger_name: string;
  passenger_phone: string;
  earliest_pickup: string;
  latest_pickup: string;
  pickup_buffer_minutes: string;
  flight_number: string;
  terminal: string;
  customer_email: string;
  special_requirements: string;
  required_vehicle_tag_ids: string[];
  allow_split: boolean;
  passengers_per_vehicle: string;
}

export function RidesPage() {
  const { token, role } = useAuth();
  const features = useFeatures();

  const {
    assign: assignOptimal,
    assignRange,
    busy: assignBusy,
    phase: assignState,
    kind: assignKind,
    result: assignResult,
    error: assignError,
  } = useAssign();
  const prevAssignState = useRef(assignState);

  const [statusFilter, setStatusFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDate, setSelectedDate] = useState(todayDateStr());
  const [page, setPage] = useState(0);

  // Dispatcher toolbar state ------------------------------------------------
  const [unassigning, setUnassigning] = useState(false);
  const [showOptimizeRange, setShowOptimizeRange] = useState(false);
  const [rangeFrom, setRangeFrom] = useState(todayDateStr());
  const [rangeTo, setRangeTo] = useState(todayDateStr());
  const [rangeError, setRangeError] = useState<string | null>(null);

  // Debounce search input.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearchTerm(searchInput);
      setPage(0);
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const isSearching = searchTerm.length > 0;

  const query = useMemo(() => {
    const parts: string[] = ["type=RIDE"];
    if (statusFilter) parts.push(`status=${statusFilter}`);
    if (searchTerm) {
      parts.push(`search=${encodeURIComponent(searchTerm)}`);
    } else if (selectedDate) {
      parts.push(`service_date=${encodeURIComponent(selectedDate)}`);
    }
    parts.push(`skip=${page * PAGE_SIZE}`, `limit=${PAGE_SIZE}`);
    return parts.join("&");
  }, [statusFilter, searchTerm, selectedDate, page]);

  const fetchRides = useCallback(
    () => listOrdersPaginated(token!, query),
    [token, query],
  );
  const { data: result, isLoading, error: ridesError, refetch } = usePolling(
    fetchRides,
    POLL,
  );
  const rides = result?.data ?? null;
  const total = result?.total ?? 0;

  const isToday = selectedDate === todayDateStr();
  const displayDate = new Date(selectedDate + "T12:00:00").toLocaleDateString(
    "en-US",
    {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: isToday ? undefined : "numeric",
    },
  );

  function shiftDate(delta: number) {
    const d = new Date(selectedDate + "T12:00:00");
    d.setDate(d.getDate() + delta);
    const pad = (n: number) => String(n).padStart(2, "0");
    setSelectedDate(
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    );
    setPage(0);
  }

  // Detail drawer ----------------------------------------------------------
  const [selectedRideId, setSelectedRideId] = useState<string | null>(null);
  const [detailRide, setDetailRide] = useState<Order | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [groupSiblings, setGroupSiblings] = useState<Order[]>([]);

  const openDetail = useCallback(
    async (id: string) => {
      setSelectedRideId(id);
      setDetailLoading(true);
      try {
        const ride = await getOrder(token!, id);
        setDetailRide(ride);
        if (ride.ride_group_id) {
          // Pull every sibling so the drawer can show the whole split group.
          const all = await listOrders(
            token!,
            `type=RIDE&limit=50`,
          );
          setGroupSiblings(
            all.filter((o) => o.ride_group_id === ride.ride_group_id),
          );
        } else {
          setGroupSiblings([]);
        }
      } finally {
        setDetailLoading(false);
      }
    },
    [token],
  );

  const closeDetail = useCallback(() => {
    setSelectedRideId(null);
    setDetailRide(null);
    setGroupSiblings([]);
  }, []);

  // Detail actions ---------------------------------------------------------
  const { addToast } = useToast();
  const [actionLoading, setActionLoading] = useState<
    "cancel" | "dispatch" | "reschedule" | null
  >(null);
  const [showReschedule, setShowReschedule] = useState(false);
  const [rescheduleFrom, setRescheduleFrom] = useState("");
  const [rescheduleTo, setRescheduleTo] = useState("");

  const handleDispatchRide = useCallback(async () => {
    if (!detailRide) return;
    setActionLoading("dispatch");
    try {
      const res = await dispatchOrderNow(token!, detailRide.id);
      addToast(res.message, res.assigned ? "success" : "error");
      if (res.assigned) {
        await openDetail(detailRide.id);
        refetch();
      }
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Dispatch failed",
        "error",
      );
    } finally {
      setActionLoading(null);
    }
  }, [detailRide, token, addToast, openDetail, refetch]);

  const handleCancelRide = useCallback(async () => {
    if (!detailRide) return;
    const confirmed = window.confirm(
      `Cancel ride #${detailRide.id.slice(0, 8)}? This cannot be undone.`,
    );
    if (!confirmed) return;
    setActionLoading("cancel");
    try {
      await cancelOrder(token!, detailRide.id);
      addToast("Ride cancelled", "success");
      await openDetail(detailRide.id);
      refetch();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Cancel failed",
        "error",
      );
    } finally {
      setActionLoading(null);
    }
  }, [detailRide, token, addToast, openDetail, refetch]);

  const handleReschedule = useCallback(async () => {
    if (!detailRide || !rescheduleFrom || !rescheduleTo) return;
    if (new Date(rescheduleFrom) >= new Date(rescheduleTo)) {
      addToast("Window end must be after start", "error");
      return;
    }
    setActionLoading("reschedule");
    try {
      await rescheduleOrder(token!, detailRide.id, {
        earliest_delivery: new Date(rescheduleFrom).toISOString(),
        latest_delivery: new Date(rescheduleTo).toISOString(),
      });
      addToast("Ride rescheduled", "success");
      setShowReschedule(false);
      setRescheduleFrom("");
      setRescheduleTo("");
      await openDetail(detailRide.id);
      refetch();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Reschedule failed",
        "error",
      );
    } finally {
      setActionLoading(null);
    }
  }, [detailRide, rescheduleFrom, rescheduleTo, token, addToast, openDetail, refetch]);

  // Toolbar action: bulk-unassign all assigned rides for the current day.
  const handleUnassign = useCallback(async () => {
    setUnassigning(true);
    try {
      const res = await triggerUnassign(token!, selectedDate);
      if (res.unassigned_count > 0) {
        addToast(
          `${res.unassigned_count} order${res.unassigned_count !== 1 ? "s" : ""} unassigned for ${displayDate}`,
          "success",
        );
      } else {
        addToast(`No assigned orders found for ${displayDate}`, "info");
      }
      refetch();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Failed to unassign — try again",
        "error",
      );
    } finally {
      setUnassigning(false);
    }
  }, [token, selectedDate, displayDate, addToast, refetch]);

  // Toolbar action: open range planner that solves N consecutive days at once.
  const handleOptimizeRange = useCallback(async () => {
    setRangeError(null);
    if (!rangeFrom || !rangeTo) {
      setRangeError("Both dates required");
      return;
    }
    if (rangeFrom > rangeTo) {
      setRangeError("End date must be on or after start");
      return;
    }
    const spanDays = Math.floor(
      (new Date(rangeTo + "T00:00:00").getTime() - new Date(rangeFrom + "T00:00:00").getTime())
        / 86_400_000,
    ) + 1;
    if (spanDays > 14) {
      setRangeError("Maximum 14-day span");
      return;
    }
    const ok = await assignRange(rangeFrom, rangeTo);
    if (ok) setShowOptimizeRange(false);
  }, [rangeFrom, rangeTo, assignRange]);

  // Surface assign-optimal result toasts so a dispatcher who clicks the
  // batch button from RidesPage gets the same feedback they'd get on
  // OrdersPage. AssignContext is global; OrdersPage's effect only fires
  // when that page is mounted, so we duplicate it here.
  useEffect(() => {
    if (prevAssignState.current === assignState) return;
    prevAssignState.current = assignState;
    if (assignState === "done" && assignResult) {
      const { routes_created, unassigned_count } = assignResult;
      const assigned = routes_created > 0
        ? `${routes_created} route${routes_created !== 1 ? "s" : ""} created`
        : "No routes created";
      const unassigned = unassigned_count > 0
        ? `, ${unassigned_count} order${unassigned_count !== 1 ? "s" : ""} could not be assigned`
        : "";
      if (assignKind === "range") {
        addToast(
          `Range plan ready: ${assignResult.assigned_count ?? 0} assigned across ${assignResult.span_days ?? "selected"} day${
            assignResult.span_days === 1 ? "" : "s"
          }${unassigned}`,
          routes_created > 0 ? "success" : "info",
        );
      } else {
        addToast(`${assigned}${unassigned}`, routes_created > 0 ? "success" : "info");
      }
      refetch();
    } else if (assignState === "error" && assignError) {
      addToast(assignError, "error");
    }
  }, [assignState, assignKind, assignResult, assignError, addToast, refetch]);

  // Create modal -----------------------------------------------------------
  const [showCreate, setShowCreate] = useState(false);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="px-6 pt-5 pb-0 shrink-0 border-b border-slate-200">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold text-slate-900">Rides</h1>
            {/* Date nav */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => shiftDate(-1)}
                className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-150"
              >
                <ChevronLeft size={15} />
              </button>
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => {
                  setSelectedDate(e.target.value);
                  setPage(0);
                }}
                className="border border-slate-200 rounded-md px-2 py-1 text-xs text-slate-600 bg-white focus:outline-none focus:border-accent-500"
              />
              <button
                onClick={() => shiftDate(1)}
                className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-150"
              >
                <ChevronRight size={15} />
              </button>
            </div>
            <span className="text-xs text-slate-400 tabular-nums">
              {isSearching
                ? "All dates"
                : `${displayDate} · ${total} ride${total !== 1 ? "s" : ""}`}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search
                size={13}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                type="text"
                placeholder="Search rides…"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-xs border border-slate-200 rounded-md bg-white text-slate-700 focus:outline-none focus:border-accent-500 w-44"
              />
            </div>
            <Link
              to="/rides/reports"
              className="inline-flex items-center gap-1.5 h-8 px-3 border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-medium rounded-md transition-colors"
            >
              <BarChart2 size={13} />
              Reports
            </Link>
            {canRunDispatcherActions(role) && !isSearching && (
              <>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => void assignOptimal(selectedDate)}
                  disabled={assignBusy || unassigning}
                  title={`Optimally assign all pending orders for ${displayDate}`}
                  data-testid="rides-assign-optimally-btn"
                >
                  <Zap size={13} />
                  {assignBusy
                    ? (assignKind === "range" ? "Optimizing…" : "Assigning…")
                    : "Assign Optimally"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void handleUnassign()}
                  disabled={unassigning || assignBusy}
                  title={`Unassign all assigned orders for ${displayDate} (excludes in-transit & delivered)`}
                  data-testid="rides-unassign-day-btn"
                >
                  <RotateCcw size={13} />
                  {unassigning ? "Unassigning…" : "Unassign Day"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setRangeError(null);
                    setShowOptimizeRange(true);
                  }}
                  disabled={assignBusy || unassigning}
                  title="Plan a multi-day range in one solve"
                  data-testid="rides-optimize-range-btn"
                >
                  <CalendarRange size={13} />
                  Optimize Range
                </Button>
              </>
            )}
            <Button
              variant="primary"
              size="sm"
              onClick={() => setShowCreate(true)}
              data-testid="create-ride-btn"
            >
              <Plus size={13} />
              New ride
            </Button>
          </div>
        </div>

        <Tabs
          tabs={STATUS_TABS}
          active={statusFilter}
          onChange={(id) => {
            setStatusFilter(id);
            setPage(0);
          }}
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto px-6 py-4 thin-scrollbar">
        {isLoading && !rides ? (
          <div className="py-16 text-center text-sm text-slate-400">Loading…</div>
        ) : ridesError && !rides ? (
          <div className="py-16 text-center">
            <p className="text-sm text-red-600">Failed to load rides</p>
            <button
              onClick={refetch}
              className="mt-2 text-xs text-accent-600 hover:underline"
            >
              Try again
            </button>
          </div>
        ) : !rides?.length ? (
          <div className="py-16 text-center text-sm text-slate-400">
            {isSearching
              ? `No rides match "${searchTerm}".`
              : "No rides for this day."}
          </div>
        ) : (
          <table data-testid="rides-table" className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  ID
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Passenger
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Status
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Route
                </th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">
                  Pickup
                </th>
              </tr>
            </thead>
            <tbody>
              {rides.map((ride) => {
                const flightNumber = readFlightNumber(ride.flight_info);
                return (
                  <tr
                    key={ride.id}
                    data-testid="ride-row"
                    onClick={() => void openDetail(ride.id)}
                    className={`border-b border-slate-100 cursor-pointer transition-colors duration-100 ${
                      selectedRideId === ride.id
                        ? "bg-orange-50"
                        : "hover:bg-slate-50"
                    }`}
                  >
                    <td className="py-2.5 px-3">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-slate-700">
                          {ride.id.slice(0, 8)}
                        </span>
                        {ride.ride_group_id && (
                          <span
                            className="text-[10px] text-accent-600"
                            title="Split ride"
                          >
                            ⤴
                          </span>
                        )}
                      </div>
                      <p className="text-[10px] text-slate-400 truncate max-w-[180px] mt-0.5">
                        {(ride.passenger_count ?? 1)} pax
                        {flightNumber ? ` · ${flightNumber}` : ""}
                      </p>
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="text-xs text-slate-800 truncate max-w-[180px]">
                        {ride.passenger_name || ride.customer_email}
                      </div>
                      {ride.passenger_phone && (
                        <div className="text-[10px] text-slate-400 truncate max-w-[180px] mt-0.5 tabular-nums">
                          {ride.passenger_phone}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      <StatusBadge value={ride.status} />
                    </td>
                    <td className="py-2.5 px-3 text-xs text-slate-600 truncate max-w-[260px]">
                      <span className="text-slate-800">
                        {shortAddress(ride.pickup_address)}
                      </span>
                      <span className="text-slate-400"> → </span>
                      <span className="text-slate-800">
                        {shortAddress(ride.delivery_address)}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-xs text-slate-400 tabular-nums whitespace-nowrap">
                      {ride.earliest_pickup
                        ? new Date(ride.earliest_pickup).toLocaleString([], {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—"}
                    </td>
                  </tr>
                );
              })}
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

      {/* Detail slide-over */}
      <SlideOver
        open={!!selectedRideId}
        onClose={closeDetail}
        title={detailRide ? `#${detailRide.id.slice(0, 8)}` : "Ride"}
        subtitle={
          detailRide
            ? detailRide.passenger_name || detailRide.customer_email
            : undefined
        }
      >
        {detailLoading ? (
          <div className="p-8 text-center text-sm text-slate-400">Loading…</div>
        ) : detailRide ? (
          <div className="px-5 py-4 space-y-5">
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge value={detailRide.status} />
              <StatusBadge value={detailRide.priority} />
              {detailRide.ride_group_id && (
                <span className="text-[10px] uppercase tracking-wide font-medium text-accent-600 bg-accent-50 px-1.5 py-0.5 rounded">
                  Split · {groupSiblings.length || 1}
                </span>
              )}
            </div>

            <div>
              <FieldRow label="Pickup" value={detailRide.pickup_address} />
              <FieldRow label="Drop-off" value={detailRide.delivery_address} />
              {detailRide.earliest_pickup && detailRide.latest_pickup && (
                <FieldRow
                  label="Pickup window"
                  value={`${new Date(detailRide.earliest_pickup).toLocaleString()} — ${new Date(detailRide.latest_pickup).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
                />
              )}
              <FieldRow
                label="Passengers"
                value={detailRide.passenger_count ?? 1}
              />
              {detailRide.passenger_name && (
                <FieldRow label="Name" value={detailRide.passenger_name} />
              )}
              {detailRide.passenger_phone && (
                <FieldRow label="Phone" value={detailRide.passenger_phone} />
              )}
              <FieldRow label="Email" value={detailRide.customer_email} />
              {readFlightNumber(detailRide.flight_info) && (
                <FieldRow
                  label="Flight"
                  value={
                    <span className="inline-flex items-center gap-1 text-accent-600">
                      <Plane size={11} />
                      {readFlightNumber(detailRide.flight_info)}
                      {readTerminal(detailRide.flight_info)
                        ? ` · ${readTerminal(detailRide.flight_info)}`
                        : ""}
                    </span>
                  }
                />
              )}
              {detailRide.special_requirements && (
                <FieldRow
                  label="Requirements"
                  value={detailRide.special_requirements}
                />
              )}
              {detailRide.required_vehicle_tags &&
                detailRide.required_vehicle_tags.length > 0 && (
                  <FieldRow
                    label="Vehicle tags"
                    value={
                      <span className="flex flex-wrap gap-1 justify-end">
                        {detailRide.required_vehicle_tags.map((tag) => (
                          <span
                            key={tag.id}
                            className="px-1.5 py-0.5 rounded-full text-[10px] font-medium"
                            style={{
                              backgroundColor: tag.color,
                              color: "#fff",
                            }}
                          >
                            {tag.name}
                          </span>
                        ))}
                      </span>
                    }
                  />
                )}
              {detailRide.assigned_courier_id && (
                <FieldRow
                  label="Courier"
                  value={
                    <span className="font-mono text-xs">
                      {detailRide.assigned_courier_id.slice(0, 8)}
                    </span>
                  }
                />
              )}
              <FieldRow
                label="Created"
                value={new Date(detailRide.created_at).toLocaleString()}
              />
            </div>

            {/* Dispatcher actions */}
            {(detailRide.status === "PENDING"
              || detailRide.status === "ASSIGNED") && (
              <div className="space-y-2 pt-2 border-t border-slate-100">
                <div className="flex flex-wrap items-center gap-2">
                  {detailRide.status === "PENDING" && (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => void handleDispatchRide()}
                      disabled={actionLoading === "dispatch"}
                      data-testid="ride-dispatch-now-btn"
                    >
                      {actionLoading === "dispatch"
                        ? "Dispatching…"
                        : "Dispatch now"}
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      // Pre-fill with the existing window so the dispatcher
                      // can nudge instead of typing from scratch.
                      if (detailRide.earliest_delivery)
                        setRescheduleFrom(detailRide.earliest_delivery.slice(0, 16));
                      if (detailRide.latest_delivery)
                        setRescheduleTo(detailRide.latest_delivery.slice(0, 16));
                      setShowReschedule((v) => !v);
                    }}
                    data-testid="ride-reschedule-toggle-btn"
                  >
                    {showReschedule ? "Hide reschedule" : "Reschedule"}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => void handleCancelRide()}
                    disabled={actionLoading === "cancel"}
                    data-testid="ride-cancel-btn"
                  >
                    {actionLoading === "cancel" ? "Cancelling…" : "Cancel ride"}
                  </Button>
                </div>
                {showReschedule && (
                  <div className="border border-slate-200 bg-slate-50/50 rounded-md p-2.5 space-y-2">
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-[11px] text-slate-500 mb-1">
                          New window start
                        </label>
                        <input
                          type="datetime-local"
                          value={rescheduleFrom}
                          onChange={(e) => setRescheduleFrom(e.target.value)}
                          className="w-full border border-slate-200 rounded-md px-2 py-1.5 text-xs bg-white focus:outline-none focus:border-accent-500"
                          data-testid="ride-reschedule-from"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] text-slate-500 mb-1">
                          New window end
                        </label>
                        <input
                          type="datetime-local"
                          value={rescheduleTo}
                          onChange={(e) => setRescheduleTo(e.target.value)}
                          className="w-full border border-slate-200 rounded-md px-2 py-1.5 text-xs bg-white focus:outline-none focus:border-accent-500"
                          data-testid="ride-reschedule-to"
                        />
                      </div>
                    </div>
                    <div className="flex justify-end">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void handleReschedule()}
                        disabled={
                          actionLoading === "reschedule"
                          || !rescheduleFrom
                          || !rescheduleTo
                        }
                        data-testid="ride-reschedule-confirm-btn"
                      >
                        {actionLoading === "reschedule" ? "Saving…" : "Save new window"}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {groupSiblings.length > 1 && (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">
                  Group ({groupSiblings.length} vehicles)
                </p>
                <div className="space-y-1">
                  {groupSiblings.map((m, idx) => (
                    <button
                      key={m.id}
                      onClick={() => void openDetail(m.id)}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-md border text-left transition-colors ${
                        m.id === detailRide.id
                          ? "border-accent-300 bg-accent-50/40"
                          : "border-slate-100 bg-slate-50 hover:bg-slate-100"
                      }`}
                    >
                      <div>
                        <div className="text-xs font-medium text-slate-700">
                          Vehicle {idx + 1} of {groupSiblings.length}
                        </div>
                        <div className="text-[11px] text-slate-500 mt-0.5">
                          {m.passenger_count} pax
                          {m.assigned_courier_id && (
                            <>
                              {" · "}
                              <span className="font-mono">
                                {m.assigned_courier_id.slice(0, 8)}
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                      <StatusBadge value={m.status} />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="p-8 text-center text-sm text-slate-400">Not found.</div>
        )}
      </SlideOver>

      {/* Create modal */}
      <Modal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="New ride"
        wide
      >
        <RideCreateForm
          features={features}
          token={token}
          onCreated={() => {
            setShowCreate(false);
            refetch();
          }}
        />
      </Modal>

      {/* Optimize Range modal */}
      <Modal
        open={showOptimizeRange}
        onClose={() => setShowOptimizeRange(false)}
        title="Optimize a multi-day range"
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-500">
            Solve a window of up to 14 consecutive days in one pass. Pending
            orders with delivery times inside the range get assigned; the
            solver can reschedule SCHEDULED/BULK orders to other days for a
            better global plan.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>From</label>
              <input
                type="date"
                value={rangeFrom}
                onChange={(e) => setRangeFrom(e.target.value)}
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>To</label>
              <input
                type="date"
                value={rangeTo}
                onChange={(e) => setRangeTo(e.target.value)}
                className={inputCls}
              />
            </div>
          </div>
          {rangeError && (
            <p className="text-xs text-red-600">{rangeError}</p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowOptimizeRange(false)}
              disabled={assignBusy}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleOptimizeRange()}
              disabled={assignBusy}
            >
              {assignBusy && assignKind === "range" ? "Optimizing…" : "Optimize range"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function shortAddress(addr: string): string {
  const idx = addr.indexOf(",");
  return idx > 0 ? addr.slice(0, idx).trim() : addr.trim();
}

function readFlightNumber(info: Record<string, unknown> | null | undefined): string | null {
  if (!info || typeof info !== "object") return null;
  const v = (info as { flight_number?: unknown }).flight_number;
  return typeof v === "string" && v ? v : null;
}

function readTerminal(info: Record<string, unknown> | null | undefined): string | null {
  if (!info || typeof info !== "object") return null;
  const v = (info as { terminal?: unknown }).terminal;
  return typeof v === "string" && v ? v : null;
}

// ---------------------------------------------------------------------------
// Create form (lives inside Modal — Modal already handles scroll & escape)
// ---------------------------------------------------------------------------

function RideCreateForm({
  features,
  token,
  onCreated,
}: {
  features: ReturnType<typeof useFeatures>;
  token: string | null;
  onCreated: () => void;
}) {
  const [state, setState] = useState<RideFormState>(RIDE_INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTags = useCallback(
    () => (token ? listVehicleTags(token) : Promise.resolve([] as VehicleTag[])),
    [token],
  );
  const { data: vehicleTagsList } = usePolling(fetchTags, 300_000);

  function update<K extends keyof RideFormState>(key: K, value: RideFormState[K]) {
    setState((prev) => ({ ...prev, [key]: value }));
  }

  function handlePickupSelect(r: GeocodeResult) {
    setState((prev) => ({
      ...prev,
      pickup_address: r.display_name,
      pickup_lat: String(r.lat),
      pickup_lon: String(r.lon),
    }));
  }

  function handleDeliverySelect(r: GeocodeResult) {
    setState((prev) => ({
      ...prev,
      delivery_address: r.display_name,
      delivery_lat: String(r.lat),
      delivery_lon: String(r.lon),
    }));
  }

  function toggleTag(tagId: string) {
    setState((prev) => ({
      ...prev,
      required_vehicle_tag_ids: prev.required_vehicle_tag_ids.includes(tagId)
        ? prev.required_vehicle_tag_ids.filter((id) => id !== tagId)
        : [...prev.required_vehicle_tag_ids, tagId],
    }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    if (!state.pickup_lat || !state.pickup_lon) {
      setError("Please pick a pickup address from the dropdown");
      return;
    }
    if (!state.delivery_lat || !state.delivery_lon) {
      setError("Please pick a drop-off address from the dropdown");
      return;
    }
    setSubmitting(true);
    try {
      const flight: Record<string, unknown> | null =
        state.flight_number || state.terminal
          ? {
              flight_number: state.flight_number || undefined,
              terminal: state.terminal || undefined,
            }
          : null;

      const payload: OrderCreate = {
        type: "RIDE",
        priority: "NORMAL",
        pickup_address: state.pickup_address,
        pickup_lat: parseFloat(state.pickup_lat),
        pickup_lon: parseFloat(state.pickup_lon),
        delivery_address: state.delivery_address,
        delivery_lat: parseFloat(state.delivery_lat),
        delivery_lon: parseFloat(state.delivery_lon),
        earliest_pickup: state.earliest_pickup,
        latest_pickup: state.latest_pickup,
        earliest_delivery: state.earliest_pickup,
        latest_delivery: state.latest_pickup,
        customer_email: state.customer_email,
        passenger_count: parseInt(state.passenger_count || "1", 10),
        passenger_name: state.passenger_name || undefined,
        passenger_phone: state.passenger_phone || undefined,
        flight_info: flight,
        special_requirements: state.special_requirements || undefined,
        pickup_buffer_minutes_override: state.pickup_buffer_minutes
          ? parseInt(state.pickup_buffer_minutes, 10)
          : undefined,
        required_vehicle_tag_ids: state.required_vehicle_tag_ids.length
          ? state.required_vehicle_tag_ids
          : undefined,
        allow_split: state.allow_split || undefined,
        passengers_per_vehicle:
          state.allow_split && state.passengers_per_vehicle
            ? parseInt(state.passengers_per_vehicle, 10)
            : undefined,
      };
      await createOrder(token, payload);
      setState(RIDE_INITIAL);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create ride");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      {error && (
        <div className="border-l-2 border-l-red-500 pl-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div>
          <label className={labelCls}>Passenger name</label>
          <input
            type="text"
            value={state.passenger_name}
            onChange={(e) => update("passenger_name", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Phone</label>
          <input
            type="text"
            value={state.passenger_phone}
            onChange={(e) => update("passenger_phone", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Passengers</label>
          <input
            type="number"
            min={1}
            value={state.passenger_count}
            onChange={(e) => update("passenger_count", e.target.value)}
            className={inputCls}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Customer email (for tracking link)</label>
          <input
            type="email"
            required
            value={state.customer_email}
            onChange={(e) => update("customer_email", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Special requirements</label>
          <input
            type="text"
            value={state.special_requirements}
            onChange={(e) => update("special_requirements", e.target.value)}
            className={inputCls}
          />
        </div>
      </div>

      <div>
        <label className={labelCls}>Pickup address</label>
        <AddressAutocomplete
          label=""
          placeholder="Search pickup address…"
          value={state.pickup_address}
          onSelect={handlePickupSelect}
          required
        />
        {state.pickup_lat && state.pickup_lon && (
          <p className="text-[11px] text-slate-400 mt-1 tabular-nums">
            {Number(state.pickup_lat).toFixed(5)}, {Number(state.pickup_lon).toFixed(5)}
          </p>
        )}
      </div>

      <div>
        <label className={labelCls}>Drop-off address</label>
        <AddressAutocomplete
          label=""
          placeholder="Search drop-off address…"
          value={state.delivery_address}
          onSelect={handleDeliverySelect}
          required
        />
        {state.delivery_lat && state.delivery_lon && (
          <p className="text-[11px] text-slate-400 mt-1 tabular-nums">
            {Number(state.delivery_lat).toFixed(5)}, {Number(state.delivery_lon).toFixed(5)}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div>
          <label className={labelCls}>Earliest pickup</label>
          <input
            type="datetime-local"
            required
            value={state.earliest_pickup}
            onChange={(e) => update("earliest_pickup", e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Latest pickup (passenger ready time)</label>
          <input
            type="datetime-local"
            required
            value={state.latest_pickup}
            onChange={(e) => update("latest_pickup", e.target.value)}
            className={inputCls}
          />
        </div>
        {features.ride_pickup_buffer_enabled && (
          <div>
            <label className={labelCls}>Driver-early buffer (min)</label>
            <input
              type="number"
              value={state.pickup_buffer_minutes}
              onChange={(e) => update("pickup_buffer_minutes", e.target.value)}
              className={inputCls}
            />
          </div>
        )}
      </div>

      {features.ride_airport_features_enabled && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Flight number (optional)</label>
            <input
              type="text"
              value={state.flight_number}
              onChange={(e) => update("flight_number", e.target.value)}
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Terminal (optional)</label>
            <input
              type="text"
              value={state.terminal}
              onChange={(e) => update("terminal", e.target.value)}
              className={inputCls}
            />
          </div>
        </div>
      )}

      <div>
        <label className={labelCls}>Vehicle requirements (optional)</label>
        {(!vehicleTagsList || vehicleTagsList.length === 0) ? (
          <p className="text-xs text-slate-500">
            No vehicle tags defined yet. Manage them in{" "}
            <Link to="/settings" className="text-accent-600 hover:underline">
              Settings → Vehicle tags
            </Link>
            .
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {vehicleTagsList.map((tag) => {
              const selected = state.required_vehicle_tag_ids.includes(tag.id);
              return (
                <button
                  key={tag.id}
                  type="button"
                  onClick={() => toggleTag(tag.id)}
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

      <div>
        <label className={labelCls}>Vehicle assignment</label>
        {parseInt(state.passenger_count || "1", 10) > 4 && !state.allow_split && (
          <div className="mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
            <span className="font-medium">Heads-up:</span> {state.passenger_count} passengers
            is a lot for a single vehicle — consider allowing this ride to be split.
          </div>
        )}
        <label className="flex items-start gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={state.allow_split}
            onChange={(e) => update("allow_split", e.target.checked)}
            className="mt-0.5"
          />
          <span>
            Allow this ride to be split across multiple vehicles
            <span className="block text-[11px] text-slate-500 mt-0.5">
              When the group exceeds the per-vehicle ceiling, the system creates
              separate orders the dispatcher can assign to different cars.
            </span>
          </span>
        </label>
        {state.allow_split && (
          <div className="mt-3 ml-6 max-w-xs">
            <label className={labelCls}>Passengers per vehicle</label>
            <input
              type="number"
              min={1}
              max={20}
              placeholder="Default: 4 (sedan)"
              value={state.passengers_per_vehicle}
              onChange={(e) => update("passengers_per_vehicle", e.target.value)}
              className={inputCls}
            />
            <p className="mt-1 text-[11px] text-slate-500">
              Leave blank to use the tenant default.
            </p>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
        <Button
          type="submit"
          variant="primary"
          size="md"
          disabled={submitting}
        >
          {submitting ? "Creating…" : "Create ride"}
        </Button>
      </div>
    </form>
  );
}

