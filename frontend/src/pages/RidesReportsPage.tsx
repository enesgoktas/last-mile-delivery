import { useEffect, useState } from "react";
import { CheckCircle2, Clock, Plane, Truck, Users } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { useFeatures } from "../hooks/useFeatures";
import {
  getAirportPunctuality,
  getFleetUtilization,
  getRideAnalytics,
} from "../api/client";
import type {
  AirportPunctualityResponse,
  FleetUtilizationResponse,
  RideAnalyticsResponse,
} from "../types";

export function RidesReportsPage() {
  const { token } = useAuth();
  const features = useFeatures();
  const [data, setData] = useState<RideAnalyticsResponse | null>(null);
  const [fleet, setFleet] = useState<FleetUtilizationResponse | null>(null);
  const [airport, setAirport] = useState<AirportPunctualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !features.rides_enabled) return;
    let cancelled = false;
    const requests: Promise<void>[] = [
      getRideAnalytics(token).then((d) => {
        if (!cancelled) setData(d);
      }),
      getFleetUtilization(token).then((d) => {
        if (!cancelled) setFleet(d);
      }),
    ];
    if (features.ride_airport_features_enabled) {
      requests.push(
        getAirportPunctuality(token).then((d) => {
          if (!cancelled) setAirport(d);
        }),
      );
    }
    Promise.allSettled(requests).then((results) => {
      if (cancelled) return;
      const firstFailed = results.find((r) => r.status === "rejected");
      if (firstFailed && firstFailed.status === "rejected") {
        setError(
          firstFailed.reason instanceof Error
            ? firstFailed.reason.message
            : "Failed to load some reports",
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, [token, features.rides_enabled, features.ride_airport_features_enabled]);

  if (!features.rides_enabled) {
    return (
      <div className="px-8 py-8 max-w-6xl mx-auto w-full">
        <h1 className="text-xl font-semibold text-slate-900">Ride reports</h1>
        <p className="text-sm text-slate-500 mt-2">
          Passenger transport is not enabled for this tenant.
        </p>
      </div>
    );
  }

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto w-full">
      <h1 className="text-xl font-semibold text-slate-900">Ride reports</h1>
      <p className="text-sm text-slate-500 mt-0.5 mb-6">
        Passenger transport activity across all time.
      </p>

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      {data === null && !error && (
        <div className="text-sm text-slate-400">Loading…</div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <StatCard icon={Users} label="Total rides" value={data.total_rides} />
            <StatCard icon={CheckCircle2} label="Completed" value={data.completed_rides} />
            <StatCard icon={Clock} label="Upcoming" value={data.upcoming_rides} />
            {features.ride_airport_features_enabled && (
              <StatCard icon={Plane} label="Airport pickups" value={data.airport_pickups} />
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Section title="Status breakdown">
              <StatusRow label="Pending" value={data.by_status.pending} />
              <StatusRow label="Assigned" value={data.by_status.assigned} />
              <StatusRow label="In transit" value={data.by_status.in_transit} />
              <StatusRow label="Delivered" value={data.by_status.delivered} accent />
              <StatusRow label="Cancelled" value={data.by_status.cancelled} muted />
              <StatusRow label="Failed" value={data.by_status.failed} muted />
            </Section>

            <Section title="Top passengers">
              {data.top_passengers.length === 0 && (
                <div className="text-xs text-slate-400 px-1">No named passengers yet.</div>
              )}
              {data.top_passengers.map((p) => (
                <div
                  key={p.passenger_name}
                  className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-b-0"
                >
                  <div className="text-sm text-slate-800">{p.passenger_name}</div>
                  <div className="flex items-center gap-3 text-xs text-slate-500 tabular-nums">
                    <span>{p.ride_count} rides</span>
                    <span className="text-emerald-600">{p.completed_count} ✓</span>
                    {p.cancelled_count > 0 && (
                      <span className="text-slate-400">{p.cancelled_count} ✗</span>
                    )}
                  </div>
                </div>
              ))}
            </Section>
          </div>

          {/* ── Fleet utilization ── */}
          <h2 className="text-sm font-semibold text-slate-700 mt-8 mb-3 flex items-center gap-2">
            <Truck size={14} className="text-slate-400" />
            Fleet utilization
          </h2>
          {fleet === null ? (
            <div className="text-sm text-slate-400">Loading…</div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-3 mb-4">
                <StatCard icon={Truck} label="Routes" value={fleet.total_route_count} />
                <StatCard
                  icon={Truck}
                  label="Total km"
                  value={Math.round(fleet.total_distance_km)}
                />
                <StatCard
                  icon={Clock}
                  label="Total hours"
                  value={Math.round(fleet.total_hours)}
                />
              </div>
              <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
                <div className="grid grid-cols-[1fr_90px_110px_90px_90px] px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400 bg-slate-50 border-b border-slate-200">
                  <div>Courier</div>
                  <div className="text-right">Routes</div>
                  <div className="text-right">Distance (km)</div>
                  <div className="text-right">Hours</div>
                  <div className="text-right">Orders</div>
                </div>
                {fleet.rows.length === 0 && (
                  <div className="px-4 py-6 text-sm text-slate-400">
                    No completed routes yet.
                  </div>
                )}
                {fleet.rows.slice(0, 20).map((row) => (
                  <div
                    key={row.courier_id}
                    className="grid grid-cols-[1fr_90px_110px_90px_90px] items-center px-4 py-2 border-b border-slate-100 last:border-b-0 text-sm tabular-nums"
                  >
                    <div className="text-slate-800 font-mono text-xs">
                      {row.courier_id.slice(0, 12)}…
                    </div>
                    <div className="text-right">{row.route_count}</div>
                    <div className="text-right">{row.total_distance_km.toFixed(1)}</div>
                    <div className="text-right">{row.total_hours.toFixed(1)}</div>
                    <div className="text-right text-slate-500">{row.order_count}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* ── Airport punctuality (gated) ── */}
          {features.ride_airport_features_enabled && (
            <>
              <h2 className="text-sm font-semibold text-slate-700 mt-8 mb-3 flex items-center gap-2">
                <Plane size={14} className="text-slate-400" />
                Airport pickup punctuality
              </h2>
              {airport === null ? (
                <div className="text-sm text-slate-400">Loading…</div>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-3 mb-4">
                    <StatCard
                      icon={Plane}
                      label="Total airport pickups"
                      value={airport.overall_total}
                    />
                    <StatCard
                      icon={CheckCircle2}
                      label="On time"
                      value={airport.overall_on_time}
                    />
                    <StatCard
                      icon={Clock}
                      label="On-time rate"
                      value={
                        airport.overall_total === 0
                          ? "—"
                          : `${(airport.overall_on_time_rate * 100).toFixed(1)}%`
                      }
                    />
                  </div>
                  <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
                    <div className="grid grid-cols-[1fr_90px_90px_110px] px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400 bg-slate-50 border-b border-slate-200">
                      <div>Terminal</div>
                      <div className="text-right">Total</div>
                      <div className="text-right">On time</div>
                      <div className="text-right">Rate</div>
                    </div>
                    {airport.rows.length === 0 && (
                      <div className="px-4 py-6 text-sm text-slate-400">
                        No completed airport pickups yet.
                      </div>
                    )}
                    {airport.rows.map((row) => (
                      <div
                        key={row.terminal}
                        className="grid grid-cols-[1fr_90px_90px_110px] items-center px-4 py-2 border-b border-slate-100 last:border-b-0 text-sm tabular-nums"
                      >
                        <div className="text-slate-800">{row.terminal}</div>
                        <div className="text-right">{row.total}</div>
                        <div className="text-right text-emerald-600">{row.on_time}</div>
                        <div className="text-right">
                          {(row.on_time_rate * 100).toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Users;
  label: string;
  value: number | string;
}) {
  return (
    <div className="border border-slate-200 rounded-md px-4 py-3 bg-white">
      <div className="flex items-center gap-2 text-slate-400 text-xs">
        <Icon size={13} />
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-slate-200 rounded-md bg-white">
      <div className="px-4 py-2 border-b border-slate-200 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
        {title}
      </div>
      <div className="px-4 py-2.5">{children}</div>
    </div>
  );
}

function StatusRow({
  label,
  value,
  accent,
  muted,
}: {
  label: string;
  value: number;
  accent?: boolean;
  muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="text-slate-700">{label}</span>
      <span
        className={[
          "tabular-nums font-medium",
          accent ? "text-emerald-600" : muted ? "text-slate-400" : "text-slate-800",
        ].join(" ")}
      >
        {value}
      </span>
    </div>
  );
}
