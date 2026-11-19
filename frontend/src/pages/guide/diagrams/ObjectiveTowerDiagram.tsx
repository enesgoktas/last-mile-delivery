/**
 * Diagram E — lexicographic objective tower.
 *
 * 4 stacked tiers, top to bottom in priority order. Plain-English caption
 * inside each tier so the diagram stands alone.
 */
type Tier = {
  symbol: string;
  name: string;
  caption: string;
};

const TIERS: Tier[] = [
  {
    symbol: "Z₁",
    name: "unserved_count_weighted",
    caption: "URGENT served first. Always.",
  },
  {
    symbol: "Z₂",
    name: "lateness + overtime (min)",
    caption: "Then: minimise late minutes and overtime.",
  },
  {
    symbol: "Z₃",
    name: "generalized_cost",
    caption: "Then: distance + fuel + labor + spread.",
  },
  {
    symbol: "Z₄",
    name: "stability_distance",
    caption: "Then (in tactical replans): prefer plans similar to the current one.",
  },
];

const TIER_HEIGHT = 70;
const TIER_GAP = 12;
const TIER_WIDTH = 380;
const LEFT_PAD = 60;
const TOP_PAD = 30;

export function ObjectiveTowerDiagram() {
  const totalHeight = TOP_PAD + TIERS.length * TIER_HEIGHT + (TIERS.length - 1) * TIER_GAP + 30;

  return (
    <figure className="my-8">
      <svg
        viewBox={`0 0 ${LEFT_PAD + TIER_WIDTH + 30} ${totalHeight}`}
        className="w-full max-w-md mx-auto block"
        role="img"
        aria-label="Lexicographic objective tower: Z1 unserved, Z2 lateness/overtime, Z3 generalised cost, Z4 stability distance."
      >
        {/* Vertical "tiebreaker order" arrow on the left */}
        <line
          x1={LEFT_PAD - 24}
          y1={TOP_PAD + 6}
          x2={LEFT_PAD - 24}
          y2={totalHeight - 30}
          stroke="#94a3b8"
          strokeWidth={1.25}
          markerEnd="url(#arrowE)"
        />
        <defs>
          <marker id="arrowE" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>
        <text
          x={LEFT_PAD - 32}
          y={(TOP_PAD + totalHeight - 30) / 2}
          textAnchor="middle"
          fontSize={10}
          fontFamily="ui-monospace, monospace"
          fill="#64748b"
          transform={`rotate(-90, ${LEFT_PAD - 32}, ${(TOP_PAD + totalHeight - 30) / 2})`}
        >
          tiebreaker order
        </text>

        {/* Tiers */}
        {TIERS.map((t, i) => {
          const y = TOP_PAD + i * (TIER_HEIGHT + TIER_GAP);
          // Slightly stronger tone for higher-priority tiers
          const opacity = 1 - i * 0.12;
          return (
            <g key={t.symbol}>
              <rect
                x={LEFT_PAD}
                y={y}
                width={TIER_WIDTH}
                height={TIER_HEIGHT}
                rx={4}
                fill="#fff7ed"
                stroke="#f97316"
                strokeWidth={1}
                opacity={opacity}
              />
              <text
                x={LEFT_PAD + 18}
                y={y + 24}
                fontSize={14}
                fontFamily="ui-monospace, monospace"
                fontWeight={600}
                fill="#9a3412"
              >
                {t.symbol}
              </text>
              <text
                x={LEFT_PAD + 56}
                y={y + 24}
                fontSize={11.5}
                fontFamily="ui-monospace, monospace"
                fill="#0f172a"
              >
                {t.name}
              </text>
              <text
                x={LEFT_PAD + 18}
                y={y + 47}
                fontSize={11}
                fontFamily="ui-sans-serif, system-ui, sans-serif"
                fill="#475569"
              >
                {t.caption}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="text-xs text-slate-500 text-center mt-3">
        Lexicographic ordering. Z₁ wins; only break ties on Z₂; only break those on Z₃.
      </figcaption>
    </figure>
  );
}
