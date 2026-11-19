/**
 * Diagram C — solver internals (rich_alns).
 *
 * The ALNS loop in the centre, with HGS pool, SP recombination, CP-SAT
 * micro-repair, and the Cython hot-path drawn as side modules. Outer
 * dotted box represents the decomposition pipeline that wraps everything
 * for instances above the size threshold.
 */
export function SolverInternalsDiagram() {
  return (
    <figure className="my-8">
      <svg
        viewBox="0 0 820 480"
        className="w-full max-w-3xl mx-auto block"
        role="img"
        aria-label="Rich ALNS internal architecture: ALNS loop, HGS pool, SP recombination, CP-SAT micro-repair, Cython hot path, outer decomposition pipeline."
      >
        <defs>
          <marker id="arrowC" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>

        {/* Outer decomposition box (dotted) */}
        <rect
          x={20}
          y={20}
          width={780}
          height={440}
          rx={8}
          fill="none"
          stroke="#cbd5e1"
          strokeWidth={1}
          strokeDasharray="4 4"
        />
        <text x={32} y={42} fontSize={11} fontFamily="ui-monospace, monospace" fill="#64748b">
          decomposition pipeline (n &gt; 200): zone-cluster → solve each → merge
        </text>

        {/* Greedy seed */}
        <rect x={40} y={70} width={130} height={40} rx={4} fill="white" stroke="#cbd5e1" />
        <text x={105} y={88} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fontWeight={500} fill="#0f172a">
          greedy_seed
        </text>
        <text x={105} y={101} textAnchor="middle" fontSize={9} fill="#64748b">
          initial feasible plan
        </text>

        {/* ALNS loop — central rounded box */}
        <rect x={210} y={140} width={360} height={200} rx={8} fill="#fff7ed" stroke="#f97316" strokeWidth={1.25} />
        <text x={390} y={162} textAnchor="middle" fontSize={12} fontFamily="ui-monospace, monospace" fontWeight={600} fill="#9a3412">
          ALNS main loop
        </text>

        {/* Loop nodes */}
        <LoopNode cx={280} cy={210} label="destroy" sub="6 ops" />
        <LoopNode cx={500} cy={210} label="repair" sub="4 ops" />
        <LoopNode cx={500} cy={290} label="accept?" sub="RRT" />
        <LoopNode cx={280} cy={290} label="update" sub="weights" />

        {/* Loop arrows (clockwise) */}
        <CurvedArrow d="M 312 210 Q 390 200 468 210" />
        <CurvedArrow d="M 500 234 Q 500 262 500 268" />
        <CurvedArrow d="M 468 290 Q 390 300 312 290" />
        <CurvedArrow d="M 280 268 Q 280 244 280 234" />

        {/* Operator legends */}
        <text x={222} y={336} fontSize={9} fontFamily="ui-monospace, monospace" fill="#64748b">
          random · shaw · worst_cost · route · time_window · compatibility
        </text>
        <text x={222} y={350} fontSize={9} fontFamily="ui-monospace, monospace" fill="#64748b">
          greedy · regret_k · cost_normalized · compat_first
        </text>

        {/* Greedy seed → ALNS loop arrow */}
        <line x1={170} y1={90} x2={210} y2={210} stroke="#94a3b8" strokeWidth={1.25} markerEnd="url(#arrowC)" />

        {/* Cython hot-path strip — long thin rectangle through destroy/repair */}
        <rect
          x={210}
          y={196}
          width={360}
          height={28}
          rx={4}
          fill="none"
          stroke="#475569"
          strokeWidth={1}
          strokeDasharray="2 2"
          opacity={0.55}
        />
        <text x={580} y={213} fontSize={9} fontFamily="ui-monospace, monospace" fill="#475569">
          Cython hot path
        </text>

        {/* HGS pool — top-right side module */}
        <SideModule x={620} y={70} w={170} h={80} title="HGS pool" lines={["feasible elites", "infeasible elites", "SREX crossover"]} />

        {/* SP recombination — middle-right */}
        <SideModule x={620} y={170} w={170} h={70} title="SP recombination" lines={["route pool ≤ 5000", "set-partitioning solve"]} />

        {/* CP-SAT — bottom-right */}
        <SideModule x={620} y={260} w={170} h={70} title="CP-SAT micro-repair" lines={["small-subset exact", "15s budget"]} />

        {/* Side-module arrows in/out of ALNS loop */}
        <line x1={570} y1={170} x2={620} y2={120} stroke="#94a3b8" strokeWidth={1} markerEnd="url(#arrowC)" />
        <line x1={620} y1={120} x2={570} y2={180} stroke="#94a3b8" strokeWidth={1} markerEnd="url(#arrowC)" />
        <line x1={570} y1={210} x2={620} y2={205} stroke="#94a3b8" strokeWidth={1} markerEnd="url(#arrowC)" />
        <line x1={570} y1={290} x2={620} y2={295} stroke="#94a3b8" strokeWidth={1} markerEnd="url(#arrowC)" />

        {/* Output → best solution */}
        <rect x={40} y={380} width={170} height={50} rx={4} fill="white" stroke="#cbd5e1" />
        <text x={125} y={402} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fontWeight={500} fill="#0f172a">
          best Solution
        </text>
        <text x={125} y={416} textAnchor="middle" fontSize={9} fill="#64748b">
          lexicographic incumbent
        </text>

        <line x1={210} y1={290} x2={210} y2={400} stroke="#94a3b8" strokeWidth={1.25} markerEnd="url(#arrowC)" />
      </svg>
      <figcaption className="text-xs text-slate-500 text-center mt-3">
        rich_alns internals. The dashed strip indicates the Cython-compiled inner loop.
      </figcaption>
    </figure>
  );
}

function LoopNode({ cx, cy, label, sub }: { cx: number; cy: number; label: string; sub: string }) {
  return (
    <g>
      <rect x={cx - 32} y={cy - 18} width={64} height={36} rx={3} fill="white" stroke="#f97316" strokeWidth={1} />
      <text x={cx} y={cy - 1} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fontWeight={500} fill="#9a3412">
        {label}
      </text>
      <text x={cx} y={cy + 11} textAnchor="middle" fontSize={9} fill="#9a3412">
        {sub}
      </text>
    </g>
  );
}

function CurvedArrow({ d }: { d: string }) {
  return (
    <path
      d={d}
      fill="none"
      stroke="#f97316"
      strokeWidth={1.25}
      markerEnd="url(#arrowC)"
    />
  );
}

function SideModule({
  x,
  y,
  w,
  h,
  title,
  lines,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  lines: string[];
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={4} fill="white" stroke="#cbd5e1" />
      <text x={x + w / 2} y={y + 18} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fontWeight={500} fill="#0f172a">
        {title}
      </text>
      {lines.map((line, i) => (
        <text
          key={i}
          x={x + w / 2}
          y={y + 36 + i * 13}
          textAnchor="middle"
          fontSize={9.5}
          fill="#64748b"
        >
          {line}
        </text>
      ))}
    </g>
  );
}
