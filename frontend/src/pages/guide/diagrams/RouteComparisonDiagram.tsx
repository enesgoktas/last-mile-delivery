import { useState } from "react";

/**
 * Diagram D — interactive route comparison.
 *
 * Same 12 customers, same depot, two assignments. The user clicks a
 * segmented control above the SVG to swap between scenarios. Numbers are
 * illustrative; caption says so.
 */
const DEPOT: [number, number] = [310, 200];

// Same 12 customers as Diagram A, kept verbatim so the two diagrams are
// visually consistent and the reader can recognise the layout.
const CUSTOMERS: { id: string; pos: [number, number] }[] = [
  { id: "R1",  pos: [350,  70] },
  { id: "R2",  pos: [430, 100] },
  { id: "R3",  pos: [490, 160] },
  { id: "R4",  pos: [510, 230] },
  { id: "R5",  pos: [470, 310] },
  { id: "R6",  pos: [400, 360] },
  { id: "R7",  pos: [310, 380] },
  { id: "R8",  pos: [220, 360] },
  { id: "R9",  pos: [140, 320] },
  { id: "R10", pos: [ 90, 250] },
  { id: "R11", pos: [100, 170] },
  { id: "R12", pos: [180, 100] },
];

const COLORS = ["#f97316", "#475569", "#6366f1"]; // orange, slate, indigo

type Scenario = "human" | "rich_alns";

// Each entry is the ordered list of customer IDs for one vehicle's route.
// Depot is implied at the start and end.
const ASSIGNMENTS: Record<Scenario, string[][]> = {
  rich_alns: [
    // Three clean zone-coherent routes with no crossings.
    ["R1", "R2", "R3", "R4"],            // NE
    ["R5", "R6", "R7", "R8"],            // S
    ["R9", "R10", "R11", "R12"],         // W
  ],
  human: [
    // Sweep-heuristic-ish but deliberately interleaved → routes criss-cross.
    ["R1", "R5", "R9", "R12"],           // spans full circle
    ["R2", "R6", "R10", "R3"],           // backtracks across centre
    ["R4", "R7", "R11", "R8"],           // criss-cross
  ],
};

// Illustrative metrics per scenario. The relative gap (~60% extra distance,
// less balanced load) is in the same ballpark as what we measured swapping
// the human-dispatcher baseline for rich_alns on synthetic_60.
const METRICS: Record<Scenario, {
  totalDistanceKm: number;
  vehiclesUsed: number;
  served: number;
  loadByRoute: number[];
}> = {
  rich_alns: {
    totalDistanceKm: 78,
    vehiclesUsed: 3,
    served: 12,
    loadByRoute: [4, 4, 4],
  },
  human: {
    totalDistanceKm: 124,
    vehiclesUsed: 3,
    served: 12,
    loadByRoute: [4, 4, 4],
  },
};

function lookup(id: string): [number, number] {
  const c = CUSTOMERS.find((x) => x.id === id);
  if (!c) throw new Error(`unknown customer ${id}`);
  return c.pos;
}

function routePolyline(stops: string[]): string {
  const pts: [number, number][] = [DEPOT, ...stops.map(lookup), DEPOT];
  return pts.map(([x, y]) => `${x},${y}`).join(" ");
}

export function RouteComparisonDiagram() {
  const [scenario, setScenario] = useState<Scenario>("human");
  const m = METRICS[scenario];
  const routes = ASSIGNMENTS[scenario];

  return (
    <figure className="my-8">
      {/* Segmented control */}
      <div className="mb-3 flex items-center justify-center">
        <div className="inline-flex border border-slate-300 rounded-md overflow-hidden text-xs font-mono">
          <button
            type="button"
            onClick={() => setScenario("human")}
            className={[
              "px-4 py-1.5 transition-colors",
              scenario === "human"
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50",
            ].join(" ")}
          >
            human dispatcher
          </button>
          <button
            type="button"
            onClick={() => setScenario("rich_alns")}
            className={[
              "px-4 py-1.5 border-l border-slate-300 transition-colors",
              scenario === "rich_alns"
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50",
            ].join(" ")}
          >
            rich_alns
          </button>
        </div>
      </div>

      <svg
        viewBox="0 0 600 420"
        className="w-full max-w-2xl mx-auto block"
        role="img"
        aria-label={`Route comparison: ${scenario === "human" ? "human dispatcher baseline" : "rich_alns optimised"} assignment of 12 customers`}
      >
        {/* Routes */}
        {routes.map((stops, i) => (
          <polyline
            key={`${scenario}-${i}`}
            fill="none"
            stroke={COLORS[i]}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            points={routePolyline(stops)}
            opacity={0.85}
          />
        ))}

        {/* Customer dots */}
        {CUSTOMERS.map((c) => {
          // Find which route this customer belongs to in the current scenario,
          // for matching dot colour.
          const groupIdx = routes.findIndex((r) => r.includes(c.id));
          const color = groupIdx >= 0 ? COLORS[groupIdx] : "#94a3b8";
          return (
            <g key={c.id}>
              <circle
                cx={c.pos[0]}
                cy={c.pos[1]}
                r={6}
                fill="white"
                stroke={color}
                strokeWidth={2}
              />
              <text
                x={c.pos[0]}
                y={c.pos[1] - 12}
                textAnchor="middle"
                fontSize={10}
                fontFamily="ui-monospace, monospace"
                fill="#475569"
              >
                {c.id}
              </text>
            </g>
          );
        })}

        {/* Depot */}
        <rect
          x={DEPOT[0] - 9}
          y={DEPOT[1] - 9}
          width={18}
          height={18}
          fill="#0f172a"
          rx={2}
        />
        <text
          x={DEPOT[0]}
          y={DEPOT[1] + 28}
          textAnchor="middle"
          fontSize={11}
          fontFamily="ui-monospace, monospace"
          fontWeight={500}
          fill="#0f172a"
        >
          depot
        </text>
      </svg>

      {/* Stat row underneath */}
      <div className="mt-4 grid grid-cols-3 gap-4 max-w-md mx-auto text-center">
        <Stat label="total distance" value={`${m.totalDistanceKm} km`} />
        <Stat label="vehicles used" value={`${m.vehiclesUsed}`} />
        <Stat label="served" value={`${m.served} / 12`} />
      </div>

      <figcaption className="text-xs text-slate-500 text-center mt-4 max-w-xl mx-auto">
        Mock data for illustration. The real Compare Routes feature in the dispatcher UI runs the same
        comparison on actual orders and persists the metrics.
      </figcaption>
    </figure>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-lg font-semibold text-slate-900 font-mono">{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-slate-500 mt-0.5">{label}</div>
    </div>
  );
}
