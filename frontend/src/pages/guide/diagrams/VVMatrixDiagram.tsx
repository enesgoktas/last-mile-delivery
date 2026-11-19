/**
 * Diagram F — V&V activity grid.
 *
 * 32 activity cells in 4 categories (A: Verification, B: Validation,
 * C: Conceptual, D: Process), color-coded by status. Hover for the
 * activity description (uses native title attribute, no popper).
 */
type Status = "DONE" | "PARTIAL" | "PLANNED" | "ELSEWHERE";

type Activity = {
  id: string;
  status: Status;
  desc: string;
};

// Mirrors backend/tests/validation_and_verification/README.md exactly.
const VERIFICATION: Activity[] = [
  { id: "A1",  status: "ELSEWHERE", desc: "Independent feasibility validator (8 hard-constraint classes)" },
  { id: "A2",  status: "ELSEWHERE", desc: "Algorithmic unit tests (113 tests)" },
  { id: "A3",  status: "ELSEWHERE", desc: "Integration / dispatch-flow tests" },
  { id: "A4",  status: "ELSEWHERE", desc: "Cython hot-path equivalent to Python reference" },
  { id: "A5",  status: "ELSEWHERE", desc: "Replay regression vs baseline snapshot" },
  { id: "A6",  status: "ELSEWHERE", desc: "Synthetic benchmark regression suite" },
  { id: "A7",  status: "DONE",      desc: "Determinism audit — same seed → same served set" },
  { id: "A8",  status: "DONE",      desc: "Boundary / edge-case suite (empty, 1-order, 1-vehicle, etc.)" },
  { id: "A9",  status: "PLANNED",   desc: "Numerical / unit consistency check (Tier 2)" },
  { id: "A10", status: "PLANNED",   desc: "Algorithm convergence trace (Tier 2)" },
  { id: "A11", status: "PLANNED",   desc: "Optimality gap on small instances (Tier 2)" },
  { id: "A12", status: "PLANNED",   desc: "Scalability / stress test (Tier 3)" },
  { id: "A13", status: "PARTIAL",   desc: "Cross-solver agreement (Compare Routes; systematic version planned)" },
];

const VALIDATION: Activity[] = [
  { id: "B1",  status: "ELSEWHERE", desc: "Same-day multi-solver comparison (Compare Routes)" },
  { id: "B2",  status: "ELSEWHERE", desc: "Operational baseline (sweep heuristic)" },
  { id: "B3",  status: "DONE",      desc: "Solomon VRPTW benchmark (56 instances, gap-to-BKS)" },
  { id: "B4",  status: "DONE",      desc: "Sensitivity analysis (sensitivity_sweep.py)" },
  { id: "B5",  status: "PLANNED",   desc: "Multi-day statistical comparison (Tier 2)" },
  { id: "B6",  status: "PARTIAL",   desc: "Multi-baseline comparison (vs OR-Tools, human; NN/random planned)" },
  { id: "B7",  status: "PLANNED",   desc: "Real-world fidelity spot-check (Tier 3)" },
  { id: "B8",  status: "PLANNED",   desc: "Robustness / failure-mode tests (Tier 3)" },
  { id: "B9",  status: "PLANNED",   desc: "UAT / domain-expert review (Tier 3)" },
  { id: "B10", status: "DONE",      desc: "Comparison vs published algorithm performance (BKS table)" },
  { id: "B11", status: "DONE",      desc: "Always-on feasibility audit in CI" },
];

const CONCEPTUAL: Activity[] = [
  { id: "C1", status: "DONE",    desc: "Formal problem statement (docs/optimization_model.md)" },
  { id: "C2", status: "DONE",    desc: "Model assumptions register (12 named MAs)" },
  { id: "C3", status: "PLANNED", desc: "Input data sanity / quality audit (Tier 2)" },
  { id: "C4", status: "PLANNED", desc: "Requirements traceability matrix (Tier 3)" },
  { id: "C5", status: "PLANNED", desc: "Face validity / domain-expert review (Tier 3)" },
];

const PROCESS: Activity[] = [
  { id: "D1", status: "DONE",    desc: "V&V chapter / report (docs/verification_and_validation.md)" },
  { id: "D2", status: "PLANNED", desc: "Risk register (Tier 3)" },
  { id: "D3", status: "PARTIAL", desc: "Decision log (VRP_REWRITE_PLAN.md covers some)" },
];

const STATUS_BG: Record<Status, string> = {
  DONE: "bg-emerald-100 border-emerald-300 text-emerald-900",
  PARTIAL: "bg-amber-100 border-amber-300 text-amber-900",
  PLANNED: "bg-sky-100 border-sky-300 text-sky-900",
  ELSEWHERE: "bg-slate-100 border-slate-300 text-slate-700",
};

const STATUS_LEGEND: { label: string; status: Status }[] = [
  { label: "DONE here", status: "DONE" },
  { label: "PARTIAL", status: "PARTIAL" },
  { label: "PLANNED", status: "PLANNED" },
  { label: "ELSEWHERE in codebase", status: "ELSEWHERE" },
];

function Cell({ a }: { a: Activity }) {
  return (
    <div
      title={`${a.id} — ${a.status}: ${a.desc}`}
      className={[
        "h-10 flex items-center justify-center rounded-sm border text-[11px] font-mono",
        STATUS_BG[a.status],
      ].join(" ")}
    >
      {a.id}
    </div>
  );
}

function CategoryRow({
  letter,
  title,
  activities,
}: {
  letter: string;
  title: string;
  activities: Activity[];
}) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-3 items-start">
      <div className="text-xs font-mono text-slate-500 pt-2.5">
        <span className="text-slate-900 font-semibold">{letter}.</span> {title}
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {activities.map((a) => (
          <Cell key={a.id} a={a} />
        ))}
      </div>
    </div>
  );
}

export function VVMatrixDiagram() {
  return (
    <figure className="my-8">
      <div className="space-y-3 rounded-md border border-slate-200 p-5 bg-white">
        <CategoryRow letter="A" title="Verification" activities={VERIFICATION} />
        <CategoryRow letter="B" title="Validation" activities={VALIDATION} />
        <CategoryRow letter="C" title="Conceptual" activities={CONCEPTUAL} />
        <CategoryRow letter="D" title="Process" activities={PROCESS} />

        <div className="flex flex-wrap gap-3 pt-3 mt-3 border-t border-slate-100">
          {STATUS_LEGEND.map((l) => (
            <div key={l.status} className="flex items-center gap-1.5 text-[11px]">
              <span
                className={[
                  "inline-block w-3 h-3 rounded-sm border",
                  STATUS_BG[l.status],
                ].join(" ")}
              />
              <span className="text-slate-600">{l.label}</span>
            </div>
          ))}
        </div>
      </div>
      <figcaption className="text-xs text-slate-500 text-center mt-3">
        32 V&V activities, 4 categories. Hover any cell for the description. Live status mirrors{" "}
        <code>tests/validation_and_verification/README.md</code>.
      </figcaption>
    </figure>
  );
}
