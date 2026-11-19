import { FormEvent, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchTracking, rescheduleDelivery, TrackingApiError, TrackingResponse } from "./api";

const TrackingMap = lazy(async () => {
  const module = await import("./TrackingMap");
  return { default: module.TrackingMap };
});

const TERMINAL_STATUS = new Set(["DELIVERED", "FAILED", "CANCELLED"]);
const POLL_INTERVAL_MS = 30_000;

function parseTokenFromUrl(): string {
  const url = new URL(window.location.href);
  const queryToken = url.searchParams.get("token");
  if (queryToken) {
    return queryToken;
  }

  const segments = url.pathname.split("/").filter(Boolean);
  const maybeToken = segments[segments.length - 1];
  if (maybeToken && maybeToken !== "tracking") {
    return maybeToken;
  }
  return "";
}

function formatLocal(dateText?: string | null): string {
  if (!dateText) {
    return "Not available";
  }
  const date = new Date(dateText);
  if (Number.isNaN(date.getTime())) {
    return "Not available";
  }
  return date.toLocaleString();
}

function describeStatus(status: string): string {
  if (status === "DELIVERED") {
    return "Delivered";
  }
  if (status === "FAILED") {
    return "Delivery attempt failed";
  }
  if (status === "CANCELLED") {
    return "Delivery cancelled";
  }
  return "On the way";
}

export function App() {
  const [data, setData] = useState<TrackingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingReschedule, setSavingReschedule] = useState(false);
  const [rescheduleError, setRescheduleError] = useState<string | null>(null);

  const token = useMemo(parseTokenFromUrl, []);
  const canReschedule = data?.can_reschedule ?? false;
  const isTerminal = data ? TERMINAL_STATUS.has(data.status) : false;

  const [earliestDelivery, setEarliestDelivery] = useState("");
  const [latestDelivery, setLatestDelivery] = useState("");
  const [customerInstructions, setCustomerInstructions] = useState("");

  // Use a ref instead of state so refreshTracking never becomes stale when the
  // user edits the instructions field between polling intervals.
  const instructionsDirtyRef = useRef(false);

  const inFlightRef = useRef(false);

  // Set to true on unrecoverable errors (401/404) — stops polling without
  // requiring a re-render or adding the flag to useCallback deps.
  const stopPollingRef = useRef(false);

  const refreshTracking = useCallback(async () => {
    if (inFlightRef.current || stopPollingRef.current) {
      return;
    }
    inFlightRef.current = true;
    try {
      const result = await fetchTracking(token);
      setData(result);
      if (!instructionsDirtyRef.current) {
        setCustomerInstructions(result.customer_instructions ?? "");
      }
      setError(null);
    } catch (err) {
      if (err instanceof TrackingApiError) {
        if (err.status === 401) {
          stopPollingRef.current = true;
          setData(null);
          setError("This tracking link is invalid or expired.");
        } else if (err.status === 404) {
          stopPollingRef.current = true;
          setData(null);
          setError("Order not found for this tracking link.");
        } else {
          setError(err.message);
        }
      } else {
        setError("Unable to fetch tracking details right now.");
      }
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      setData(null);
      setLoading(false);
      setError("Tracking token is missing. Please open the link from your delivery notification.");
      return;
    }

    if (isTerminal) {
      return;
    }

    let cancelled = false;

    void refreshTracking();

    const timer = window.setInterval(async () => {
      if (cancelled) {
        return;
      }
      await refreshTracking();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isTerminal, refreshTracking, token]);

  async function onSubmitReschedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !earliestDelivery || !latestDelivery) {
      return;
    }

    const earliest = new Date(earliestDelivery);
    const latest = new Date(latestDelivery);
    if (Number.isNaN(earliest.getTime()) || Number.isNaN(latest.getTime())) {
      setRescheduleError("Please provide valid delivery windows.");
      return;
    }
    if (earliest >= latest) {
      setRescheduleError("Earliest time must be before latest time.");
      return;
    }

    setSavingReschedule(true);
    setRescheduleError(null);
    try {
      const updated = await rescheduleDelivery(
        token,
        earliest.toISOString(),
        latest.toISOString(),
        data?.can_update_instructions ? customerInstructions.trim() || undefined : undefined,
      );
      setData(updated);
      setEarliestDelivery("");
      setLatestDelivery("");
      setCustomerInstructions(updated.customer_instructions ?? "");
      instructionsDirtyRef.current = false;
    } catch (err) {
      setRescheduleError(err instanceof Error ? err.message : "Unable to reschedule right now.");
    } finally {
      setSavingReschedule(false);
    }
  }

  // RIDE orders re-label the page for the passenger; package orders keep the
  // existing delivery copy. `order_type` defaults to ON_DEMAND on the backend
  // so this branch is naturally false for legacy responses.
  const isRide = data?.order_type === "RIDE";
  const pageEyebrow = isRide ? "Live Ride" : "Live Delivery";
  const pageTitle = isRide ? "Track Your Ride" : "Track Your Order";
  const arrivalLabel = isRide ? "Driver Arrival" : "Estimated Arrival";
  const updateLabel = isRide ? "Last Driver Update" : "Last Courier Update";
  const destinationLabel = isRide ? "Drop-off" : "Delivery Address";

  return (
    <main className="tracking-page">
      <section className="tracking-card">
        <header className="tracking-header">
          <div>
            <p className="eyebrow">{pageEyebrow}</p>
            <h1>{pageTitle}</h1>
            {isRide && data?.passenger_name && (
              <p className="muted">For {data.passenger_name}</p>
            )}
          </div>
          {data && (
            <span className={`status-pill status-${data.status.toLowerCase()}`}>
              {data.status_label ?? describeStatus(data.status)}
            </span>
          )}
        </header>

        {loading && <p className="muted">Loading tracking data...</p>}
        {!loading && error && <p className="error">{error}</p>}

        {data && (
          <>
            <div className="stats-grid">
              <article>
                <h2>{arrivalLabel}</h2>
                <p>
                  {data.eta_visibility === "NONE"
                    ? "Hidden by sender"
                    : formatLocal(data.estimated_arrival)}
                </p>
              </article>
              <article>
                <h2>{updateLabel}</h2>
                <p>{formatLocal(data.last_updated)}</p>
              </article>
              <article>
                <h2>Stops Completed</h2>
                <p>
                  {data.completed_stops ?? "-"} / {data.total_stops ?? "-"}
                </p>
              </article>
              <article>
                <h2>{destinationLabel}</h2>
                <p>{data.delivery_address}</p>
              </article>
            </div>

            {data.show_live_map ? (
              <Suspense fallback={<p className="muted">Loading map...</p>}>
                <TrackingMap
                  courierLat={data.courier_lat}
                  courierLon={data.courier_lon}
                  deliveryLat={data.delivery_lat}
                  deliveryLon={data.delivery_lon}
                />
              </Suspense>
            ) : (
              <div className="terminal-note">
                <strong>Live map hidden</strong>
                <p className="muted terminal-helper">
                  This sender has disabled map sharing for this delivery.
                </p>
              </div>
            )}

            {isTerminal && (
              <div className="terminal-note">
                <strong>Status:</strong> {data.status_label ?? describeStatus(data.status)}
                <p className="muted terminal-helper">
                  Live tracking updates are paused because this delivery is in a terminal state.
                </p>
              </div>
            )}

            {data.customer_instructions ? (
              <div className="terminal-note">
                <strong>Delivery Instructions</strong>
                <p className="muted terminal-helper">{data.customer_instructions}</p>
              </div>
            ) : null}

            {canReschedule && (
              <form className="reschedule-form" onSubmit={onSubmitReschedule}>
                <h2>Request Redelivery</h2>
                <div className="field-row">
                  <label>
                    Earliest
                    <input
                      type="datetime-local"
                      value={earliestDelivery}
                      onChange={(e) => setEarliestDelivery(e.target.value)}
                      required
                    />
                  </label>
                  <label>
                    Latest
                    <input
                      type="datetime-local"
                      value={latestDelivery}
                      onChange={(e) => setLatestDelivery(e.target.value)}
                      required
                    />
                  </label>
                </div>
                {data.can_update_instructions && (
                  <label>
                    Delivery instructions
                    <textarea
                      value={customerInstructions}
                      onChange={(e) => {
                        setCustomerInstructions(e.target.value);
                        instructionsDirtyRef.current = true;
                      }}
                      rows={3}
                    />
                  </label>
                )}
                <button type="submit" disabled={savingReschedule}>
                  {savingReschedule ? "Submitting..." : "Submit Reschedule Request"}
                </button>
                {rescheduleError && <p className="error">{rescheduleError}</p>}
              </form>
            )}
          </>
        )}
      </section>
    </main>
  );
}
