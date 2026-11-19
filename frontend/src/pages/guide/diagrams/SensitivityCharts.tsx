/**
 * Diagram G — sensitivity bar charts.
 *
 * Two compact SVG bar charts side-by-side. Numbers are exactly what we
 * measured running scripts/sensitivity_sweep.py on synthetic_60 with
 * a 4-second budget per point (V&V chapter §4.1).
 */
type Point = { x: number; distance: number; served?: number };

const FLEET_SIZE_DATA: Point[] = [
  { x: 2, distance: 350.8, served: 32 },
  { x: 3, distance: 524.8, served: 52 },
  { x: 4, distance: 623.0, served: 58 },
  { x: 5, distance: 691.7, served: 60 },
  { x: 6, distance: 725.5, served: 60 },
];

const FUEL_PRICE_DATA: Point[] = [
  { x: 0,   distance: 756.0 },
  { x: 0.5, distance: 817.0 },
  { x: 1,   distance: 692.5 },
  { x: 1.5, distance: 678.1 },
  { x: 2,   distance: 681.7 },
];

type ChartProps = {
  data: Point[];
  xLabel: string;
  yLabel: string;
  showServed?: boolean;
  yMax: number;
};

const CHART_W = 320;
const CHART_H = 220;
const PAD_LEFT = 44;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 36;

function BarChart({ data, xLabel, yLabel, showServed, yMax }: ChartProps) {
  const innerW = CHART_W - PAD_LEFT - PAD_RIGHT;
  const innerH = CHART_H - PAD_TOP - PAD_BOTTOM;
  const barWidth = (innerW / data.length) * 0.55;
  const xStep = innerW / data.length;

  const barX = (i: number) =>
    PAD_LEFT + i * xStep + (xStep - barWidth) / 2;
  const barY = (v: number) => PAD_TOP + innerH * (1 - v / yMax);
  const barH = (v: number) => innerH * (v / yMax);

  // Y-axis tick values
  const ticks = [0, yMax * 0.25, yMax * 0.5, yMax * 0.75, yMax];

  // Right-axis scale for served (0 to 60)
  const servedScale = 60;
  const servedY = (v: number) => PAD_TOP + innerH * (1 - v / servedScale);

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      className="w-full max-w-xs mx-auto block"
    >
      {/* Y-axis grid + labels */}
      {ticks.map((t, i) => {
        const y = barY(t);
        return (
          <g key={i}>
            <line
              x1={PAD_LEFT}
              y1={y}
              x2={CHART_W - PAD_RIGHT}
              y2={y}
              stroke="#e2e8f0"
              strokeWidth={0.75}
            />
            <text
              x={PAD_LEFT - 6}
              y={y + 3}
              textAnchor="end"
              fontSize={9}
              fontFamily="ui-monospace, monospace"
              fill="#94a3b8"
            >
              {Math.round(t)}
            </text>
          </g>
        );
      })}

      {/* Bars */}
      {data.map((p, i) => (
        <rect
          key={i}
          x={barX(i)}
          y={barY(p.distance)}
          width={barWidth}
          height={barH(p.distance)}
          fill="#fb923c"
          opacity={0.85}
        />
      ))}

      {/* X-axis labels */}
      {data.map((p, i) => (
        <text
          key={`xl-${i}`}
          x={barX(i) + barWidth / 2}
          y={CHART_H - PAD_BOTTOM + 14}
          textAnchor="middle"
          fontSize={10}
          fontFamily="ui-monospace, monospace"
          fill="#475569"
        >
          {p.x}
        </text>
      ))}

      {/* Served-count overlay (small dots) */}
      {showServed &&
        data.map((p, i) =>
          p.served !== undefined ? (
            <circle
              key={`s-${i}`}
              cx={barX(i) + barWidth / 2}
              cy={servedY(p.served)}
              r={3.5}
              fill="#0f172a"
            />
          ) : null,
        )}
      {showServed && (
        // Connect the dots with a thin line
        <polyline
          fill="none"
          stroke="#0f172a"
          strokeWidth={1}
          opacity={0.6}
          points={data
            .filter((p) => p.served !== undefined)
            .map((p, i) => `${barX(i) + barWidth / 2},${servedY(p.served!)}`)
            .join(" ")}
        />
      )}

      {/* Axis labels */}
      <text
        x={CHART_W / 2}
        y={CHART_H - 4}
        textAnchor="middle"
        fontSize={10}
        fontFamily="ui-monospace, monospace"
        fill="#64748b"
      >
        {xLabel}
      </text>
      <text
        x={10}
        y={CHART_H / 2}
        textAnchor="middle"
        fontSize={10}
        fontFamily="ui-monospace, monospace"
        fill="#64748b"
        transform={`rotate(-90, 10, ${CHART_H / 2})`}
      >
        {yLabel}
      </text>
    </svg>
  );
}

export function SensitivityCharts() {
  return (
    <figure className="my-8">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-8">
        <div>
          <p className="text-xs font-mono text-slate-500 mb-1 text-center">
            fleet size sweep
          </p>
          <BarChart
            data={FLEET_SIZE_DATA}
            xLabel="fleet size"
            yLabel="distance (km)"
            yMax={800}
            showServed
          />
          <p className="text-[11px] text-slate-500 text-center mt-1">
            bars: distance &middot; dots: served / 60
          </p>
        </div>
        <div>
          <p className="text-xs font-mono text-slate-500 mb-1 text-center">
            fuel-price multiplier sweep
          </p>
          <BarChart
            data={FUEL_PRICE_DATA}
            xLabel="fuel multiplier"
            yLabel="distance (km)"
            yMax={900}
          />
          <p className="text-[11px] text-slate-500 text-center mt-1">
            bars: distance
          </p>
        </div>
      </div>
      <figcaption className="text-xs text-slate-500 text-center mt-4 max-w-xl mx-auto">
        synthetic_60 dataset, rich_alns, 4 s budget per point. Reproduce with{" "}
        <code>scripts/sensitivity_sweep.py</code>.
      </figcaption>
    </figure>
  );
}
