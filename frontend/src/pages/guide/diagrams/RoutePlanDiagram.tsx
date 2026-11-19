/**
 * Diagram A — route plan, visualised.
 *
 * 1 depot + 12 customers + 3 colour-coded routes. Static. The exact same
 * customer set is reused in RouteComparisonDiagram, where the assignment
 * changes between scenes.
 */
const DEPOT: [number, number] = [300, 210];

type Customer = { id: string; pos: [number, number]; group: 0 | 1 | 2 };

const CUSTOMERS: Customer[] = [
  // Group 0 (NE) — orange route
  { id: "R1",  pos: [350,  70], group: 0 },
  { id: "R2",  pos: [430, 100], group: 0 },
  { id: "R3",  pos: [490, 160], group: 0 },
  { id: "R4",  pos: [510, 230], group: 0 },
  // Group 1 (S/SE) — indigo route
  { id: "R5",  pos: [470, 310], group: 1 },
  { id: "R6",  pos: [400, 360], group: 1 },
  { id: "R7",  pos: [310, 380], group: 1 },
  { id: "R8",  pos: [220, 360], group: 1 },
  // Group 2 (W/NW) — slate route
  { id: "R9",  pos: [140, 320], group: 2 },
  { id: "R10", pos: [ 90, 250], group: 2 },
  { id: "R11", pos: [100, 170], group: 2 },
  { id: "R12", pos: [180, 100], group: 2 },
];

const GROUP_COLORS = ["#f97316", "#475569", "#6366f1"]; // orange, slate, indigo

function routePath(group: 0 | 1 | 2): [number, number][] {
  const stops = CUSTOMERS.filter((c) => c.group === group).map((c) => c.pos);
  return [DEPOT, ...stops, DEPOT];
}

function pointsAttr(pts: [number, number][]): string {
  return pts.map(([x, y]) => `${x},${y}`).join(" ");
}

export function RoutePlanDiagram() {
  return (
    <figure className="my-8">
      <svg
        viewBox="0 0 600 420"
        className="w-full max-w-2xl mx-auto block"
        role="img"
        aria-label="Route plan: one depot, twelve customer requests, three vehicles"
      >
        {/* Routes (under the dots) */}
        {[0, 1, 2].map((g) => (
          <polyline
            key={g}
            fill="none"
            stroke={GROUP_COLORS[g]}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            points={pointsAttr(routePath(g as 0 | 1 | 2))}
            opacity={0.85}
          />
        ))}

        {/* Customer dots */}
        {CUSTOMERS.map((c) => (
          <g key={c.id}>
            <circle
              cx={c.pos[0]}
              cy={c.pos[1]}
              r={6}
              fill="white"
              stroke={GROUP_COLORS[c.group]}
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
        ))}

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
      <figcaption className="text-xs text-slate-500 text-center mt-3">
        12 customer requests, 3 vehicles. Each colour is one vehicle&apos;s route, depot to depot.
      </figcaption>
    </figure>
  );
}
