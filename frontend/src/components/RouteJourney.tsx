import { StatusBadge } from "./StatusBadge";
import type { Courier, Route, RouteStop } from "../types";
import { courierAssignmentLabel } from "../utils/courierDisplay";

type RouteJourneyProps = {
  route: Route;
  courier: Courier | null;
};

function sortStops(stops: RouteStop[]): RouteStop[] {
  return [...stops].sort((a, b) => a.sequence_number - b.sequence_number);
}

function truncateAddress(address: string): string {
  if (address.length <= 88) {
    return address;
  }
  return `${address.slice(0, 85)}...`;
}

export function RouteJourney({ route, courier }: RouteJourneyProps) {
  const stops = sortStops(route.stops);
  const completed = stops.filter((s) => s.status === "COMPLETED").length;
  const failed = stops.filter((s) => s.status === "FAILED").length;
  const pending = stops.filter((s) => s.status === "PENDING").length;
  const arrived = stops.filter((s) => s.status === "ARRIVED").length;

  return (
    <article className="journey-card">
      <header className="journey-header">
        <div>
          <h3>Route {route.id.slice(0, 8)}</h3>
          <p className="muted">
            Courier: {courierAssignmentLabel(courier, route.courier_id)}
          </p>
        </div>
        <StatusBadge value={route.status} />
      </header>

      <div className="journey-stats">
        <div>
          <span>Completed</span>
          <strong>{completed}</strong>
        </div>
        <div>
          <span>Arrived</span>
          <strong>{arrived}</strong>
        </div>
        <div>
          <span>Pending</span>
          <strong>{pending}</strong>
        </div>
        <div>
          <span>Failed</span>
          <strong>{failed}</strong>
        </div>
      </div>

      {stops.length === 0 ? (
        <p className="muted">No stops in this route yet.</p>
      ) : (
        <ol className="journey-timeline">
          {stops.map((stop) => (
            <li key={stop.id} className={`journey-step ${stop.status.toLowerCase()}`}>
              <div className="journey-step-marker">
                <span>{stop.sequence_number + 1}</span>
              </div>
              <div className="journey-step-content">
                <div className="journey-step-top">
                  <strong>{stop.stop_type}</strong>
                  <StatusBadge value={stop.status} />
                </div>
                <p>{truncateAddress(stop.address)}</p>
                <p className="muted">
                  Planned: {new Date(stop.planned_arrival).toLocaleString()}
                  {stop.actual_arrival ? ` • Actual: ${new Date(stop.actual_arrival).toLocaleString()}` : ""}
                </p>
                {stop.failure_reason ? <p className="error-text">Reason: {stop.failure_reason}</p> : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}
