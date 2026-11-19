/**
 * Diagram B — system architecture block.
 *
 * Inputs (orders / couriers / OSRM matrix) flow into a VRPInput, then
 * through the solver registry to one of three adapters, then through the
 * validator on the way to dispatch.
 */
type Box = {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub?: string;
  emphasised?: boolean;
};

const BOXES: Box[] = [
  { x:  20, y:  20, w: 130, h: 44, label: "Orders DB",  sub: "PostgreSQL" },
  { x:  20, y: 100, w: 130, h: 44, label: "Couriers DB", sub: "PostgreSQL" },
  { x:  20, y: 180, w: 130, h: 44, label: "OSRM",       sub: "MLD road graph" },

  { x: 215, y:  90, w: 130, h: 64, label: "VRPInput", sub: "matrices + I/O" },

  { x: 410, y:  90, w: 140, h: 64, label: "SolverRegistry", sub: "adapter protocol" },

  { x: 615, y:  20, w: 145, h: 44, label: "rich_alns",       sub: "ALNS+HGS+SP", emphasised: true },
  { x: 615, y: 100, w: 145, h: 44, label: "ortools_cvrptw",  sub: "OR-Tools" },
  { x: 615, y: 180, w: 145, h: 44, label: "human_dispatcher", sub: "sweep heuristic" },

  { x: 410, y: 280, w: 140, h: 50, label: "VRPSolution", sub: "routes + costs" },
  { x: 215, y: 280, w: 130, h: 50, label: "Validator",   sub: "8 hard constraints" },
  { x:  20, y: 280, w: 130, h: 50, label: "Dispatch",    sub: "executor" },
];

type Edge = { from: number; to: number; midY?: number };
const EDGES: Edge[] = [
  { from: 0, to: 3 },
  { from: 1, to: 3 },
  { from: 2, to: 3 },
  { from: 3, to: 4 },
  { from: 4, to: 5 },
  { from: 4, to: 6 },
  { from: 4, to: 7 },
  { from: 5, to: 8 },
  { from: 6, to: 8 },
  { from: 7, to: 8 },
  { from: 8, to: 9 },
  { from: 9, to: 10 },
];

function boxCenter(b: Box): [number, number] {
  return [b.x + b.w / 2, b.y + b.h / 2];
}

function edgePoints(from: Box, to: Box): [number, number][] {
  // Right-anchor → left-anchor for horizontal flow; otherwise center-to-center
  // with a single mid bend.
  const fc = boxCenter(from);
  const tc = boxCenter(to);

  // Horizontal flow when on similar rows
  if (Math.abs(fc[1] - tc[1]) < 30) {
    return [
      [from.x + from.w, fc[1]],
      [to.x, tc[1]],
    ];
  }

  // Diagonal: exit right of from, enter left of to, with vertical mid-bend.
  if (to.x > from.x) {
    const exitX = from.x + from.w;
    const enterX = to.x;
    const midX = (exitX + enterX) / 2;
    return [
      [exitX, fc[1]],
      [midX, fc[1]],
      [midX, tc[1]],
      [enterX, tc[1]],
    ];
  }

  // Right-to-left flow (the bottom row): exit left of from, enter right of to.
  const exitX = from.x;
  const enterX = to.x + to.w;
  const midX = (exitX + enterX) / 2;
  return [
    [exitX, fc[1]],
    [midX, fc[1]],
    [midX, tc[1]],
    [enterX, tc[1]],
  ];
}

function pointsAttr(pts: [number, number][]): string {
  return pts.map(([x, y]) => `${x},${y}`).join(" ");
}

export function ArchitectureDiagram() {
  return (
    <figure className="my-8">
      <svg
        viewBox="0 0 800 360"
        className="w-full max-w-3xl mx-auto block"
        role="img"
        aria-label="System architecture: orders, couriers, OSRM flow into VRPInput, through solver registry to three adapters, then to validator and dispatch."
      >
        <defs>
          <marker
            id="arrowB"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>

        {/* Edges first so they sit under the boxes */}
        {EDGES.map((e, i) => (
          <polyline
            key={i}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={1.25}
            points={pointsAttr(edgePoints(BOXES[e.from], BOXES[e.to]))}
            markerEnd="url(#arrowB)"
          />
        ))}

        {/* Boxes */}
        {BOXES.map((b, i) => (
          <g key={i}>
            <rect
              x={b.x}
              y={b.y}
              width={b.w}
              height={b.h}
              rx={4}
              fill="white"
              stroke={b.emphasised ? "#f97316" : "#cbd5e1"}
              strokeWidth={b.emphasised ? 1.5 : 1}
            />
            <text
              x={b.x + b.w / 2}
              y={b.y + (b.sub ? b.h / 2 - 3 : b.h / 2 + 4)}
              textAnchor="middle"
              fontSize={12}
              fontFamily="ui-monospace, monospace"
              fontWeight={500}
              fill="#0f172a"
            >
              {b.label}
            </text>
            {b.sub && (
              <text
                x={b.x + b.w / 2}
                y={b.y + b.h / 2 + 13}
                textAnchor="middle"
                fontSize={9.5}
                fontFamily="ui-sans-serif, system-ui, sans-serif"
                fill="#64748b"
              >
                {b.sub}
              </text>
            )}
          </g>
        ))}
      </svg>
      <figcaption className="text-xs text-slate-500 text-center mt-3">
        End-to-end pipeline. The validator runs on every plan regardless of which adapter produced it.
      </figcaption>
    </figure>
  );
}
