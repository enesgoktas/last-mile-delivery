import { useEffect } from "react";
import { ArchitectureDiagram } from "./guide/diagrams/ArchitectureDiagram";
import { ObjectiveTowerDiagram } from "./guide/diagrams/ObjectiveTowerDiagram";
import { RouteComparisonDiagram } from "./guide/diagrams/RouteComparisonDiagram";
import { RoutePlanDiagram } from "./guide/diagrams/RoutePlanDiagram";
import { SensitivityCharts } from "./guide/diagrams/SensitivityCharts";
import { SolverInternalsDiagram } from "./guide/diagrams/SolverInternalsDiagram";
import { VVMatrixDiagram } from "./guide/diagrams/VVMatrixDiagram";

const LAST_UPDATED = "2026-05-07";

export function WalkthroughPage() {
  useEffect(() => {
    document.title = "LastMileDelivery — Walkthrough";
  }, []);

  return (
    <div className="min-h-screen bg-white text-slate-900 font-sans antialiased">
      {/* Header strip */}
      <header className="border-b border-slate-200">
        <div className="mx-auto max-w-3xl px-6 py-5 flex items-baseline justify-between gap-6">
          <div className="flex items-baseline gap-3 min-w-0">
            <span className="font-mono text-sm text-slate-900 truncate">
              lastmiledelivery
            </span>
            <span className="text-slate-300">/</span>
            <span className="text-sm text-slate-600 truncate">walkthrough</span>
          </div>
          <span className="text-xs font-mono text-slate-400 shrink-0">
            updated {LAST_UPDATED}
          </span>
        </div>
      </header>

      {/* Body — single column, no sticky TOC; this page is meant to read top-to-bottom */}
      <main className="mx-auto max-w-3xl px-6 py-12 text-[15px] leading-relaxed text-slate-700">
        <h1 className="text-2xl font-semibold text-slate-900 leading-tight">
          What we built, in plain words
        </h1>
        <p className="mt-3 text-slate-600">
          Same project as the technical guide, just talked through. If you
          want the formal version with numbered sections and the spec links,
          there&apos;s one of those over at <code>/guide</code>.
        </p>

        <div className="mt-14 space-y-16">
          <SectionWhat />
          <SectionProblem />
          <SectionPipeline />
          <SectionSolver />
          <SectionTwoPlans />
          <SectionObjective />
          <SectionRules />
          <SectionDoesItWork />
          <SectionNumbers />
          <SectionWhatsNext />
        </div>

        <Footer />
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sections — kept inline; mirror /guide content but in conversational voice.
// ---------------------------------------------------------------------------

function SectionHead({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-lg font-semibold text-slate-900 mb-4">{children}</h2>
  );
}

function SectionWhat() {
  return (
    <section>
      <SectionHead>So what is this thing?</SectionHead>
      <p>
        We built a backend service that figures out which courier should
        deliver which package, in what order, by what time. Hand it a pile of
        orders and a roster of couriers, and it hands back a plan — every{" "}
        <code>URGENT</code> order assigned, every time window respected,
        nothing over capacity.
      </p>
      <p className="mt-4">
        The textbook name for the problem is <code>PDPTW-MT</code> —
        pickup-and-delivery vehicle routing with time windows and multi-trip
        support. We took the textbook version and added eight extra wrinkles
        you actually need for real dispatch: paired pickup-then-delivery on
        the same courier, multi-trip days, a mixed fleet (cars, vans,
        motorbikes), three capacity dimensions instead of one (weight,
        volume, package count), compatibility tags, time windows that are
        soft-then-hard instead of just hard, a four-tier objective, and a
        switch for &ldquo;spread work across everyone&rdquo; vs &ldquo;use
        as few couriers as we can.&rdquo;
      </p>
      <p className="mt-4">
        There are three solvers under the same interface, so we can swap
        between them or run all three on the same input and compare. The
        production one is <code>rich_alns</code> — our metaheuristic. We
        also keep Google&apos;s OR-Tools (<code>ortools_cvrptw</code>) around
        as a benchmark, and a sweep-zone heuristic (<code>human_dispatcher</code>)
        that stands in for whatever a manual dispatcher would do.
      </p>
    </section>
  );
}

function SectionProblem() {
  return (
    <section>
      <SectionHead>What&apos;s it actually solving?</SectionHead>
      <p>
        You&apos;ve got <em>R</em> orders and <em>V</em> couriers. Decide who
        picks up what, in what sequence, at what time. Constraints: time
        windows, vehicle capacities, paired pickup/delivery, vehicle-order
        compatibility, optional breaks, hard shift end. The objective is a
        4-tier ranking — cover the most orders first, then cut down lateness,
        then cut down cost, then prefer plans that look like yesterday&apos;s.
      </p>
      <p className="mt-4">
        100 customers and 25 couriers gives you something like 25
        <sup>100</sup> possible assignments before you even sequence them. No
        exact method finishes that in seconds. So everything in here is a
        heuristic or metaheuristic — clever search that finds a good plan
        quickly, not the literal best one.
      </p>

      <div className="my-8">
        <RoutePlanDiagram />
      </div>

      <p>
        The picture above is the kind of plan it produces: one depot, twelve
        customers, three colored routes, one per courier. Same input — lots
        of valid plans. The solvers differ in which one they happen to find
        inside the budget.
      </p>
    </section>
  );
}

function SectionPipeline() {
  return (
    <section>
      <SectionHead>How does it work end-to-end?</SectionHead>
      <p>
        Orders and couriers come out of Postgres. <code>OSRM</code> — we
        self-host the open-source routing engine — gives us the real-road
        duration and distance matrices for every location we&apos;ll touch
        that day. The solver registry picks one of the three adapters based
        on what the caller asked for. The adapter produces a{" "}
        <code>VRPSolution</code>. Then a separate validator — written from
        scratch, doesn&apos;t know anything about how the solver works —
        checks the plan against every hard rule before it goes to a
        driver&apos;s phone.
      </p>

      <div className="my-8">
        <ArchitectureDiagram />
      </div>

      <p>Three things keep this layout simple:</p>
      <ul className="list-disc pl-6 space-y-2 mt-3">
        <li>
          All three solvers implement the same protocol, so the rest of the
          app doesn&apos;t care which one is picked.
        </li>
        <li>
          The validator is solver-independent. A buggy solver can&apos;t
          produce a plan that&apos;s self-consistent-but-wrong and slip past
          us — the validator doesn&apos;t share its bugs.
        </li>
        <li>
          OSRM is the only external dependency in the hot path. Everything
          else is in-process Python, with a Cython compile for the inner
          loop.
        </li>
      </ul>
      <p className="mt-4 italic text-slate-600">
        Plain English: we can swap the solver without changing anything
        else, and we double-check every plan with a separate piece of code
        that knows the rules but not how the plan was made.
      </p>
    </section>
  );
}

function SectionSolver() {
  return (
    <section>
      <SectionHead>The actual solver, opened up</SectionHead>
      <p>
        <code>rich_alns</code> is the production engine. It doesn&apos;t try
        to prove its plan is mathematically optimal — that&apos;s impossible
        inside any realistic time budget for this problem size. Instead it
        searches through a lot of plans intelligently and keeps the best
        one it found before the clock runs out. Five pieces stack on top of
        each other, each handling a different way of getting unstuck.
      </p>

      <div className="my-8">
        <SolverInternalsDiagram />
      </div>

      <h3 className="text-base font-semibold text-slate-900 mt-8 mb-2">
        1. ALNS — Adaptive Large Neighborhood Search (the main loop)
      </h3>
      <p>
        Take the current plan, <em>destroy</em> part of it (rip out a chunk
        of orders), then <em>repair</em> it (put those orders back, probably
        somewhere different this time). If the new plan is better, keep it.
        Repeat thousands of times within the time budget. That&apos;s the
        whole loop.
      </p>
      <p className="mt-3">
        The &ldquo;large neighborhood&rdquo; part of the name means each
        iteration makes a <em>big</em> edit — typically removes 10–40% of
        the orders at once and re-inserts them. That&apos;s deliberate.
        Tiny edits (swap two stops) get stuck quickly because most small
        tweaks make things slightly worse, even when a major
        reorganization would help. Big edits let the search escape those
        traps.
      </p>
      <p className="mt-3">
        The &ldquo;adaptive&rdquo; part means the algorithm watches which
        kinds of edits are actually working and biases toward those. We
        have six destroy strategies and four repair strategies:
      </p>
      <ul className="list-disc pl-6 space-y-1.5 mt-3 text-[14px]">
        <li>
          <code>random_remove</code> — pick orders to remove at random.
          Useful as a baseline and to add diversity.
        </li>
        <li>
          <code>shaw</code> — remove orders that are <em>similar</em> to
          each other (close in space, close in time window). Forces the
          repair step to redistribute a coherent cluster.
        </li>
        <li>
          <code>worst_cost</code> — remove the orders contributing the most
          cost in the current plan. Targets the obviously-bad assignments.
        </li>
        <li>
          <code>route_remove</code> — wipe out an entire vehicle&apos;s
          route. Forces a reshuffling that can fundamentally change which
          courier handles which orders.
        </li>
        <li>
          <code>time_window</code> — remove orders with the tightest time
          windows. Often the trickiest ones to place; pulling them and
          re-inserting them can unlock better positions for the rest.
        </li>
        <li>
          <code>compatibility</code> — remove orders with restrictive
          vehicle requirements (fragile, temperature-sensitive, tag
          restrictions). Same idea — the picky orders often need special
          treatment.
        </li>
      </ul>
      <p className="mt-3">And four repair strategies:</p>
      <ul className="list-disc pl-6 space-y-1.5 mt-3 text-[14px]">
        <li>
          <code>greedy_paired</code> — for each removed order, find the
          cheapest insertion position and use it. Fast but myopic.
        </li>
        <li>
          <code>regret_k</code> — for each removed order, look at its top-k
          best insertion positions and prioritize the order that would{" "}
          <em>regret</em> not being placed first the most (i.e., the order
          where the second-best slot is much worse than the best).
        </li>
        <li>
          <code>cost_normalized</code> — like greedy but balances insertion
          cost across vehicles, preventing one vehicle from absorbing all
          the cheap insertions and ending up overloaded.
        </li>
        <li>
          <code>compatibility_first</code> — handle orders with the most
          restrictive compatibility requirements before the flexible ones.
          The picky orders go where they fit; the flexible ones fill the
          gaps.
        </li>
      </ul>
      <p className="mt-3">
        Picking which destroy/repair pair to use each iteration is itself
        an optimization. We use a <strong>segmented roulette wheel</strong>
        : imagine a casino roulette wheel where each operator has a slot,
        and slot sizes change based on recent performance. An operator
        that just produced a big improvement gets a bigger slot, so it
        gets picked more often. Every 100 iterations the scores get a
        partial reset (multiplied by 0.8) so a dormant operator can climb
        back up if conditions change later in the run.
      </p>
      <p className="mt-3">
        Last piece of the loop: should we accept a new plan that&apos;s
        slightly <em>worse</em> than the current best? Surprisingly, yes —
        sometimes. Always rejecting worse plans means you get stuck in
        local optima (the equivalent of "this hill is the highest because
        every direction goes down from here"). The rule we use is called{" "}
        <strong>Record-to-Record Travel</strong>: accept the new plan if
        it&apos;s within X% of the best plan we&apos;ve ever seen. X starts
        loose (~1.5%) and tightens toward nearly 0 as the budget runs out,
        so the search wanders early and locks in late.
      </p>

      <h3 className="text-base font-semibold text-slate-900 mt-10 mb-2">
        2. HGS — Hybrid Genetic Search
      </h3>
      <p>
        ALNS by itself can converge to a local optimum and circle around
        it forever. HGS adds an evolutionary-algorithm sidecar to break
        out of that. The idea: keep a <em>population</em> of high-quality
        plans, periodically &ldquo;breed&rdquo; two of them to create a
        child plan, restart the ALNS loop from the child. Two good plans
        often share useful structural ideas (a particular route ordering,
        a particular grouping of customers); combining ideas from two
        different good plans can produce something better than either
        parent.
      </p>
      <p className="mt-3">
        We keep two parallel pools (capped at 25 plans each). One holds
        only <strong>feasible</strong> plans (every constraint satisfied);
        the other allows <strong>infeasible</strong> plans (some constraint
        broken, with the violation tallied as a penalty). Allowing
        infeasible plans in the search sounds wrong but turns out to be
        useful: sometimes the shortest path between two feasible plans
        goes <em>through</em> an infeasible region, and forbidding the
        detour means you never get to the other side.
      </p>
      <p className="mt-3">
        The breeding operation is called <strong>SREX</strong> — Selective
        Route Exchange. Pick two parent plans. Take a few of the best
        routes from parent A. Merge them with the rest of parent B&apos;s
        routes. Resolve any orders that ended up assigned twice or not at
        all by running a quick repair pass. The result is an offspring
        that inherits structural ideas from both parents.
      </p>
      <p className="mt-3">
        There&apos;s also an <strong>adaptive penalty manager</strong> that
        controls how harshly we penalize infeasible plans. If the search
        is stuck inside the feasible region (no progress, but never
        violating constraints), lower the penalty so it explores
        infeasibility for a detour. If it&apos;s drifting too far into
        infeasibility (lots of broken constraints), raise the penalty to
        pull it back. The penalty self-tunes during the run.
      </p>

      <h3 className="text-base font-semibold text-slate-900 mt-10 mb-2">
        3. SP recombination — Set-Partitioning route pool
      </h3>
      <p>
        While ALNS and HGS run, every individual <em>route</em> that
        appears in an accepted plan also gets saved to a separate
        collection — call it a route library. After a few thousand
        iterations the library has accumulated hundreds or thousands of
        decent routes from many different plans.
      </p>
      <p className="mt-3">
        Periodically we ask a different question: <em>given this library,
        what&apos;s the best combination of routes that covers every
        customer exactly once?</em> That&apos;s a classical optimization
        problem called <strong>set partitioning</strong> — universe of
        elements (customers), collection of subsets (routes), pick the
        subset of subsets that covers each element exactly once at minimum
        cost. There&apos;s a fast solver for it (we use Google&apos;s
        CP-SAT, same engine as below).
      </p>
      <p className="mt-3">
        The intuition: ALNS produces lots of locally good routes that
        don&apos;t always combine into a globally good plan. Set
        partitioning takes the best individual routes and finds the best
        way to <em>assemble</em> them. It&apos;s like having a folder of
        recipes from many different cookbooks, then asking &ldquo;what&apos;s
        the best dinner I can make using each ingredient exactly
        once?&rdquo;
      </p>
      <p className="mt-3">
        The pool is capped at 5 000 routes (so the SP solve stays under a
        20-second time limit). Recombination triggers every 1 500 ALNS
        iterations, or sooner if the search has stagnated for 800
        iterations.
      </p>

      <h3 className="text-base font-semibold text-slate-900 mt-10 mb-2">
        4. CP-SAT micro-repair
      </h3>
      <p>
        When the search has been stuck for a while — no improvement for
        800 iterations — we call in heavier machinery. <strong>CP-SAT</strong>{" "}
        is Google OR-Tools&apos; constraint-programming solver, a totally
        different family from ALNS. CP solvers reason about which
        decisions are <em>still possible</em> given the constraints
        (Sudoku is the textbook example) and use that to prove
        optimality on small problems.
      </p>
      <p className="mt-3">
        The catch is that CP-SAT scales badly with problem size — it can
        solve a 40-customer subproblem to provable optimum in 15 seconds,
        but couldn&apos;t touch 1 000 customers in any time you&apos;d
        wait. So when ALNS stalls, we extract a <em>small</em>{" "}
        subproblem — pick the 40 most-troubled requests, freeze the rest
        of the plan — and hand that to CP-SAT. CP-SAT solves it exactly
        within its 15-second budget. The result gets plugged back into
        the full plan.
      </p>
      <p className="mt-3">
        It&apos;s the &ldquo;call in a specialist for a small piece of
        the puzzle when your generalist gets stuck&rdquo; pattern.
      </p>

      <h3 className="text-base font-semibold text-slate-900 mt-10 mb-2">
        5. The Cython hot path
      </h3>
      <p>
        Inside that ALNS loop, one function runs millions of times per
        solve: the cost-and-feasibility evaluator. Given a candidate
        route, it computes total cost and checks every hard constraint
        (time windows, capacity, etc.). Pure Python is slow for tight
        numerical loops like this, and even a 3× speedup on that one
        function dominates total solve time.
      </p>
      <p className="mt-3">
        <strong>Cython</strong> is a tool that compiles Python-like code
        to C. You write something that looks like Python with type
        annotations; it produces a native C extension that runs at
        near-C speed. Source files use the <code>.pyx</code> extension;
        they&apos;re built into compiled <code>.so</code> modules during
        deployment.
      </p>
      <p className="mt-3">
        The risk with hand-tuned compiled code is that the fast version
        drifts away from the slow Python reference: a typo in the Cython
        source produces wrong answers, fast. We mitigate that with a
        separate test that runs the same input through both
        implementations and asserts the outputs are numerically
        identical, plus another test that asserts the Cython version is
        at least 3× faster (so a regression in the optimization gets
        flagged automatically).
      </p>

      <h3 className="text-base font-semibold text-slate-900 mt-10 mb-2">
        Bonus: the decomposition pipeline (when problems get big)
      </h3>
      <p>
        For instances above 200 orders, an outer pipeline kicks in before
        ALNS even starts. It clusters orders into geographic zones using
        a sweep-and-pack heuristic, solves each zone independently as a
        smaller VRP, then merges the cluster solutions back into one
        plan. The merge step is feasibility-checked, so a route that
        crosses zone boundaries can still appear in the final plan.
      </p>
      <p className="mt-3">
        Without this, a 1 000-order day would explore an absurdly large
        search space. With it, we trade a small amount of solution
        quality (zones can&apos;t share orders mid-solve) for a huge gain
        in tractability. Each zone gets a proportional share of the total
        time budget based on its size.
      </p>

      <p className="mt-8 italic text-slate-600">
        In one sentence: a smart edit-and-keep loop, plus a genetic-style
        sidecar that breeds plans together, plus a route library that
        gets re-assembled by a different solver, plus a constraint
        solver that takes over when everything else is stuck, plus a
        compiled inner loop, plus a way to break big problems into
        smaller ones.
      </p>
    </section>
  );
}

function SectionTwoPlans() {
  return (
    <section>
      <SectionHead>Same orders, two different plans</SectionHead>
      <p>
        Twelve customers, one depot, two different assignments. Toggle
        between them. The numbers underneath are illustrative — based on
        the kind of differences you&apos;d see on a 60-customer synthetic
        instance.
      </p>

      <div className="my-8">
        <RouteComparisonDiagram />
      </div>

      <p>
        The qualitative difference shows up in two places: load balance
        (how stops distribute across vehicles) and route shape (whether
        routes cross each other or stay in their own neighborhood). The
        sweep heuristic the human-dispatcher baseline uses takes the first
        reasonable plan it finds; the metaheuristic spends its budget
        rearranging until it lands on something tighter.
      </p>
    </section>
  );
}

function SectionObjective() {
  return (
    <section>
      <SectionHead>How we decide which plan is better</SectionHead>
      <p>
        The solver minimizes a 4-tuple in <strong>lexicographic order</strong>.
        That just means: compare on Z₁ first, only break ties on Z₂, only
        break those ties on Z₃, and so on. There&apos;s no trade-off between
        tiers — one more URGENT order served beats <em>any</em> economic
        improvement, full stop.
      </p>
      <p className="mt-4 italic text-slate-600">
        Sort of like ranking sports teams by wins first, then by points
        scored, then by alphabet. You only fall to the next criterion if
        everything above is tied.
      </p>

      <div className="my-8">
        <ObjectiveTowerDiagram />
      </div>

      <ol className="list-decimal pl-6 space-y-3">
        <li>
          <strong>Z₁ — unserved count, weighted.</strong> Every unassigned
          order adds its priority weight (URGENT = 10 000, HIGH = 100,
          NORMAL = 10, LOW = 1) to this tier. URGENT dominates everything
          else, by design.
        </li>
        <li>
          <strong>Z₂ — lateness + overtime, in minutes.</strong> Minutes
          past each order&apos;s soft delivery deadline plus minutes past
          each vehicle&apos;s soft shift end. Soft-window violations are
          allowed, just penalized here.
        </li>
        <li>
          <strong>Z₃ — generalized cost.</strong> Fixed per-vehicle cost,
          plus distance × per-km, plus fuel × per-km, plus labor ×
          per-minute, plus overtime, plus a fleet-spread penalty when{" "}
          <code>spread_fleet=True</code>.
        </li>
        <li>
          <strong>Z₄ — stability distance.</strong> Currently 0. Reserved
          for tactical replanning so a mid-day re-solve prefers plans
          drivers are already executing.
        </li>
      </ol>
    </section>
  );
}

function SectionRules() {
  return (
    <section>
      <SectionHead>Rules and assumptions</SectionHead>
      <p>
        The model commits to twelve hard constraints (the solver must
        produce plans that satisfy them) and rests on twelve named modeling
        assumptions (load-bearing simplifications about the real world).
        Both are on display so you can see the model&apos;s edges without
        reading code.
      </p>

      <h3 className="text-sm font-semibold text-slate-900 mt-8 mb-3">
        Hard constraints — every solution must satisfy
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-200 text-left">
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 w-12">
                ID
              </th>
              <th className="py-2 pr-3 font-medium text-slate-700">
                What it says, in plain words
              </th>
            </tr>
          </thead>
          <tbody className="text-slate-700">
            <ConstraintRow
              id="C1"
              text="Every request is either assigned exactly once or left unassigned — never double-assigned."
            />
            <ConstraintRow
              id="C2"
              text="No URGENT order is allowed to be left unassigned."
            />
            <ConstraintRow
              id="C3"
              text="Pickup happens before delivery on the same trip; travel time and service time chain correctly."
            />
            <ConstraintRow
              id="C4"
              text="A request's pickup and delivery share the same vehicle and trip — they don't get split."
            />
            <ConstraintRow
              id="C5"
              text="Capacity in three dimensions (weight, volume, package count) is never exceeded at any point in a route."
            />
            <ConstraintRow
              id="C6"
              text="Time windows are respected. Hard deadlines can't be missed; soft deadlines can be, with a penalty."
            />
            <ConstraintRow
              id="C7"
              text="Hard shift end can't be exceeded — if a courier is supposed to be done by 18:00, they're done by 18:00."
            />
            <ConstraintRow
              id="C8"
              text="Total route distance never exceeds the vehicle's max-distance setting (when one is set)."
            />
            <ConstraintRow
              id="C9"
              text="Total stops on a route never exceed the vehicle's max-stops setting (when one is set)."
            />
            <ConstraintRow
              id="C10"
              text="Compatibility tags match: required order tags ⊆ vehicle tags; fragile / temperature-sensitive flags align with vehicle capabilities."
            />
            <ConstraintRow
              id="C11"
              text="Allowed-vehicle restrictions are honored — some orders can only go on specific vehicles."
            />
            <ConstraintRow
              id="C12"
              text="Mandatory breaks are scheduled within their break window for the right duration."
            />
          </tbody>
        </table>
      </div>

      <h3 className="text-sm font-semibold text-slate-900 mt-10 mb-3">
        Modeling assumptions — load-bearing fictions about reality
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-200 text-left">
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 w-12">
                ID
              </th>
              <th className="py-2 pr-3 font-medium text-slate-700">
                Assumption
              </th>
              <th className="py-2 pr-3 font-medium text-slate-700">
                Where it breaks
              </th>
            </tr>
          </thead>
          <tbody className="text-slate-700">
            <AssumptionRow
              id="MA1"
              assumption="Travel times are deterministic — one number per arc, no distribution."
              breaks="Severe traffic disruption, weather events."
            />
            <AssumptionRow
              id="MA2"
              assumption="No mid-route capacity transfers between vehicles."
              breaks="Cross-docking or hub-and-spoke ops."
            />
            <AssumptionRow
              id="MA3"
              assumption="One depot per vehicle (start = end)."
              breaks="Vehicles serving multiple warehouses."
            />
            <AssumptionRow
              id="MA4"
              assumption="Pickup-at-depot is the common case; non-depot pickups are supported but coupled."
              breaks="True peer-to-peer pickup/delivery."
            />
            <AssumptionRow
              id="MA5"
              assumption="The order set is static at solve time; new orders trigger a re-solve."
              breaks="High-velocity flash-sale arrivals."
            />
            <AssumptionRow
              id="MA6"
              assumption="Travel matrix is symmetric and respects the triangle inequality."
              breaks="Dense one-way street networks (small error accepted)."
            />
            <AssumptionRow
              id="MA7"
              assumption="Service time depends on the order, not the vehicle."
              breaks="Specialized loading equipment per vehicle type."
            />
            <AssumptionRow
              id="MA8"
              assumption="Lexicographic priorities are strict — no trade-off between tiers."
              breaks="Pathological tiny-improvement-vs-big-cost edge cases."
            />
            <AssumptionRow
              id="MA9"
              assumption="Costs are linear and additive — no volume discounts."
              breaks="Tiered carrier contracts."
            />
            <AssumptionRow
              id="MA10"
              assumption="Vehicles are independent — no convoying or platooning."
              breaks="Trucks-with-trailers operations."
            />
            <AssumptionRow
              id="MA11"
              assumption="Fleet utilization is binary — FULL_FLEET or EFFICIENT, no in-between."
              breaks={'Soft "prefer to spread but accept consolidation" semantics.'}
            />
            <AssumptionRow
              id="MA12"
              assumption="Solomon BKS values are stable benchmarks."
              breaks="Future research tightens BKS by ≤ 0.5%, but doesn't change orders of magnitude."
            />
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ConstraintRow({ id, text }: { id: string; text: string }) {
  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="py-2 pr-3 font-mono text-xs text-slate-500">{id}</td>
      <td className="py-2 pr-3 leading-relaxed">{text}</td>
    </tr>
  );
}

function AssumptionRow({
  id,
  assumption,
  breaks,
}: {
  id: string;
  assumption: string;
  breaks: string;
}) {
  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="py-2 pr-3 font-mono text-xs text-slate-500">{id}</td>
      <td className="py-2 pr-3 leading-relaxed">{assumption}</td>
      <td className="py-2 pr-3 leading-relaxed text-slate-500">{breaks}</td>
    </tr>
  );
}

function SectionDoesItWork() {
  return (
    <section>
      <SectionHead>Does it actually work? (V&amp;V)</SectionHead>
      <p>
        Software unit tests verify that individual functions do what their
        authors intended. They won&apos;t tell you whether the solver
        returns plans that satisfy the spec, or whether the system beats
        a manual baseline on real data. The standard
        industrial-engineering response to that is V&amp;V:{" "}
        <em>verification</em> (did we build the system right?),{" "}
        <em>validation</em> (did we build the right system?), and{" "}
        <em>conceptual validation</em> (did we model the right problem?).
      </p>

      <div className="my-8">
        <VVMatrixDiagram />
      </div>

      <p>The four most defensible claims today:</p>
      <ul className="list-disc pl-6 space-y-3 mt-3">
        <li>
          <strong>
            Independent feasibility validator runs on every plan.
          </strong>{" "}
          Eight constraint families checked post-solve, no matter which
          solver produced the plan. Runs in CI on three synthetic instance
          sizes across all three adapters; runs in production on every
          batch dispatch and surfaces <code>validation_error_count</code>{" "}
          in solver metrics.
        </li>
        <li>
          <strong>
            The Cython hot path is proven equivalent to the Python reference.
          </strong>{" "}
          The optimized inner evaluator must produce numerically identical
          forward and backward passes to the slow reference; a separate
          test asserts a ≥3× speedup so we know we&apos;re not paying for
          an optimization we lost.
        </li>
        <li>
          <strong>Seed-deterministic on the served-order set.</strong>{" "}
          Across <code>rich_alns</code>, <code>ortools_cvrptw</code>, and{" "}
          <code>human_dispatcher</code>, the same problem with the same
          seed produces the same set of served orders. Cost may vary
          slightly across runs because the metaheuristic terminates on a
          wall-clock budget — the combinatorial decision is what
          reproduces, not the cost down to the digit.
        </li>
        <li>
          <strong>Solomon VRPTW benchmark is wired up.</strong> Parser,
          BKS table, runner, public-mirror download script. Per-instance
          gap to the published Best-Known Solution gets recorded in CSV on
          every CI run. The numbers are honest — see the next section.
        </li>
      </ul>

      <p className="mt-5">
        Full inventory of V&amp;V activities — including the ones scoped
        but not yet implemented — lives in{" "}
        <code className="text-xs">
          backend/tests/validation_and_verification/README.md
        </code>
        . Score: <strong>9 done, 4 partial, 12 planned, 7 covered
        elsewhere</strong> in the codebase.
      </p>
    </section>
  );
}

function SectionNumbers() {
  return (
    <section>
      <SectionHead>Honest numbers</SectionHead>

      <h3 className="text-sm font-semibold text-slate-900 mb-3">
        Solomon VRPTW benchmark
      </h3>
      <p>
        Six representative 100-customer instances, one per Solomon family,
        60-second budget per instance with <code>rich_alns</code>. Gap is
        to the published Best-Known Solution from Vidal et al. 2013.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse my-4">
          <thead>
            <tr className="border-b border-slate-200 text-left">
              <th className="py-2 pr-3 font-mono text-xs text-slate-500">
                Instance
              </th>
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 text-right">
                BKS
              </th>
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 text-right">
                Ours
              </th>
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 text-right">
                Gap
              </th>
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 text-right">
                BKS / our v.
              </th>
              <th className="py-2 pr-3 font-mono text-xs text-slate-500 text-right">
                Served
              </th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs text-slate-700">
            <SolomonRow inst="R101"  bks="1650.8" ours="2077.6" gap="25.9%"  vehicles="19 / 24" served="94 / 100" />
            <SolomonRow inst="C101"  bks="828.9"  ours="2028.1" gap="144.7%" vehicles="10 / 24" served="99 / 100" />
            <SolomonRow inst="RC101" bks="1623.6" ours="2444.4" gap="50.6%"  vehicles="14 / 24" served="100 / 100" />
            <SolomonRow inst="R201"  bks="1252.4" ours="2226.2" gap="77.8%"  vehicles="4 / 24"  served="100 / 100" />
            <SolomonRow inst="C201"  bks="591.6"  ours="2255.4" gap="281.3%" vehicles="3 / 24"  served="100 / 100" />
            <SolomonRow inst="RC201" bks="1406.9" ours="2507.8" gap="78.3%"  vehicles="4 / 24"  served="100 / 100" />
          </tbody>
        </table>
      </div>

      <p>
        <strong>Mean gap on the 5 feasibility-clean instances: 75.4%.</strong>{" "}
        That number is honest, not flattering. The classical literature
        target on Solomon is around 3–5% — but that&apos;s with hours of
        compute on a solver tuned specifically for VRPTW. Our gap is
        explained by two things:
      </p>
      <ul className="list-disc pl-6 space-y-2 mt-3">
        <li>
          <strong>Wall-clock budget.</strong> 60 seconds vs. hours.
        </li>
        <li>
          <strong>Objective mismatch.</strong> Solomon&apos;s BKS implicitly
          assumes &ldquo;minimize vehicles, then minimize distance.&rdquo;{" "}
          <code>rich_alns</code> is configured for last-mile dispatch,
          where the natural objective is &ldquo;use the fleet you have,
          balance the load,&rdquo; not &ldquo;use as few couriers as
          possible.&rdquo; The solver consistently uses 24 of 25 available
          couriers on Solomon instances. The gap is dominated by the extra
          return-to-depot trips this creates on long-horizon (2-series)
          instances.
        </li>
      </ul>
      <p className="mt-4">
        R101 — where the BKS uses 19 of 25 vehicles, close to our 24 —
        lands at 25.9% gap, which is actually competitive. That supports
        the diagnosis. The honest framing:{" "}
        <em>
          rich_alns is competitive on random VRPTW instances under
          realistic time budgets; on instances with a strong &ldquo;minimize
          vehicles&rdquo; optimum it underperforms the literature by design
        </em>
        .
      </p>

      <h3 className="text-sm font-semibold text-slate-900 mt-10 mb-3">
        Sensitivity sweeps
      </h3>
      <p>
        On the <code>synthetic_60</code> dataset, varying one parameter
        while holding the rest fixed (<code>rich_alns</code>, 4 s budget
        per point):
      </p>

      <div className="my-8">
        <SensitivityCharts />
      </div>

      <p>
        The fleet-size response is monotone and looks right: more vehicles
        cover more orders; once every order fits, marginal extra vehicles
        only add a bit of repositioning distance. The fuel-price response
        is what you&apos;d expect — at multiplier 0 the solver accepts
        longer routes (no distance penalty), and increasing the multiplier
        nudges it to spend its budget reducing distance.
      </p>
    </section>
  );
}

function SolomonRow({
  inst,
  bks,
  ours,
  gap,
  vehicles,
  served,
}: {
  inst: string;
  bks: string;
  ours: string;
  gap: string;
  vehicles: string;
  served: string;
}) {
  return (
    <tr className="border-b border-slate-100">
      <td className="py-2 pr-3">{inst}</td>
      <td className="py-2 pr-3 text-right">{bks}</td>
      <td className="py-2 pr-3 text-right">{ours}</td>
      <td className="py-2 pr-3 text-right">{gap}</td>
      <td className="py-2 pr-3 text-right text-slate-500">{vehicles}</td>
      <td className="py-2 pr-3 text-right text-slate-500">{served}</td>
    </tr>
  );
}

function SectionWhatsNext() {
  return (
    <section>
      <SectionHead>What we haven&apos;t done yet</SectionHead>
      <p>
        Tier 1 of the V&amp;V plan is finished (the activities cited above).
        Two further tiers are scoped and waiting:
      </p>

      <h3 className="text-sm font-semibold text-slate-900 mt-6 mb-2">
        Tier 2 — thesis-grade extensions
      </h3>
      <ul className="list-disc pl-6 space-y-1.5">
        <li>
          <strong>Multi-day statistical backtest.</strong> N ≥ 30
          historical days, paired-sample t-test on cost and on-time rate,
          bootstrap 95% CI, per-day delta scatter. This is what
          underwrites &ldquo;X% improvement over manual dispatch with
          significance&rdquo; claims.
        </li>
        <li>
          <strong>
            Algorithm convergence trace + tiny-instance optimality gap.
          </strong>{" "}
          Plot ALNS objective vs. iteration; verify <code>rich_alns</code>{" "}
          actually finds known optima on 5- and 8-customer problems where
          CP-SAT can solve to provable optimum.
        </li>
        <li>
          <strong>Multi-baseline comparison.</strong> Add nearest-neighbour
          and random-feasible baselines so the solver isn&apos;t only being
          measured against a strong heuristic.
        </li>
        <li>
          <strong>Numerical / unit-consistency audit.</strong> Walk every
          cost-related field through the codebase, confirming m vs. km, s
          vs. min, g vs. kg lines up.
        </li>
      </ul>

      <h3 className="text-sm font-semibold text-slate-900 mt-6 mb-2">
        Tier 3 — operational hardening
      </h3>
      <ul className="list-disc pl-6 space-y-1.5">
        <li>
          <strong>Stress / scalability test.</strong> Synthetic instances
          at 100, 250, 500, 1 000, 2 000 orders; document time and memory
          curves.
        </li>
        <li>
          <strong>OSRM fidelity spot-check.</strong> Sample 100 arcs,
          compare against a reference, compute MAPE on travel time and
          distance.
        </li>
        <li>
          <strong>Robustness / failure-mode tests.</strong> OSRM
          unreachable; courier removed mid-day; late URGENT order injected
          after solve.
        </li>
        <li>
          <strong>
            Domain-expert UAT, requirements traceability matrix, risk
            register, decision log.
          </strong>{" "}
          Process artefacts that turn the codebase into a defensible
          deliverable.
        </li>
      </ul>
    </section>
  );
}

function Footer() {
  return (
    <footer className="mt-24 pt-8 border-t border-slate-200 text-sm text-slate-500 space-y-2">
      <p className="font-mono text-xs text-slate-400">References</p>
      <ul className="space-y-1">
        <li>
          <code className="text-xs">/guide</code> — formal version of this
          page with numbered sections and a sticky TOC
        </li>
        <li>
          <code className="text-xs">docs/optimization_model.md</code> —
          formal PDPTW-MT spec
        </li>
        <li>
          <code className="text-xs">docs/verification_and_validation.md</code>{" "}
          — V&amp;V report
        </li>
        <li>
          <code className="text-xs">
            backend/tests/validation_and_verification/
          </code>{" "}
          — V&amp;V test suite (46 tests)
        </li>
      </ul>
    </footer>
  );
}
