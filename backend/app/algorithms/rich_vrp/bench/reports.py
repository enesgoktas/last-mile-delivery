"""Human-readable diff / table output for benchmark results."""
from __future__ import annotations

from app.algorithms.rich_vrp.bench.runner import SuiteResults


def format_table(results: SuiteResults) -> str:
    """Return a markdown table of benchmark results grouped by dataset."""
    if not results.cells:
        return "(no results)"

    adapters = sorted({c.adapter for c in results.cells})
    datasets = sorted({c.dataset for c in results.cells})

    lines: list[str] = []
    lines.append(f"## Benchmark run `{results.run_id}`\n")

    for dataset in datasets:
        lines.append(f"### {dataset}\n")
        header = "| metric | " + " | ".join(adapters) + " |"
        sep = "|---|" + "---|" * len(adapters)
        lines.append(header)
        lines.append(sep)

        metrics = [
            ("served", "served_count"),
            ("unserved", "unserved_count"),
            ("distance (km)", None),
            ("cost", "generalized_cost"),
            ("late (min)", "late_minutes"),
            ("overtime (min)", "overtime_minutes"),
            ("wall_clock (s)", "wall_clock_s"),
        ]

        cells_by_adapter = {
            adapter: [c for c in results.cells if c.dataset == dataset and c.adapter == adapter]
            for adapter in adapters
        }

        for label, attr in metrics:
            row_parts = [f"| {label} "]
            for adapter in adapters:
                cells = cells_by_adapter[adapter]
                if not cells or all(c.error for c in cells):
                    row_parts.append("| ERR ")
                    continue
                valid = [c for c in cells if not c.error]
                if attr == "wall_clock_s":
                    vals = [getattr(c, attr) for c in valid]
                    row_parts.append(f"| {sum(vals)/len(vals):.1f}s ")
                elif attr is None:
                    # distance in km
                    vals = [c.total_distance_m / 1000 for c in valid]
                    row_parts.append(f"| {sum(vals)/len(vals):.1f} ")
                else:
                    vals = [getattr(c, attr) for c in valid]
                    row_parts.append(f"| {sum(vals)/len(vals):.0f} ")
            row_parts.append("|")
            lines.append("".join(row_parts))

        lines.append("")

    return "\n".join(lines)
