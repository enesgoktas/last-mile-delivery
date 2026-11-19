"""Best-Known Solutions (BKS) for Solomon's 100-customer VRPTW instances.

Values are the published best-known total distance and minimum vehicle
count. Sources:
  - Vidal, Crainic, Gendreau, Prins (2013): "A hybrid genetic algorithm
    with adaptive diversity management for a large class of vehicle
    routing problems with time-windows."  Computers & Operations Research.
  - SINTEF VRPTW benchmark page (transport.sintef.no/vrptw).

These BKS values are widely cited and stable; small refinements
(< 0.5%) have appeared over the years (Helsgaun 2017 LKH-3, etc.) but
the values below are sufficient for the gap-to-BKS comparisons our
V&V suite makes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BKS:
    """Published best-known solution for one Solomon instance."""
    distance: float       # total distance, in Solomon units
    n_vehicles: int       # minimum vehicle count


SOLOMON_BKS: dict[str, BKS] = {
    # R1 — random, short horizon (12 instances)
    "R101": BKS(1650.80, 19),
    "R102": BKS(1486.12, 17),
    "R103": BKS(1292.68, 13),
    "R104": BKS(1007.24,  9),
    "R105": BKS(1377.11, 14),
    "R106": BKS(1252.03, 12),
    "R107": BKS(1104.66, 10),
    "R108": BKS( 960.88,  9),
    "R109": BKS(1194.73, 11),
    "R110": BKS(1118.84, 10),
    "R111": BKS(1096.72, 10),
    "R112": BKS( 982.14,  9),

    # C1 — clustered, short horizon (9 instances)
    "C101": BKS(828.94, 10),
    "C102": BKS(828.94, 10),
    "C103": BKS(828.06, 10),
    "C104": BKS(824.78, 10),
    "C105": BKS(828.94, 10),
    "C106": BKS(828.94, 10),
    "C107": BKS(828.94, 10),
    "C108": BKS(828.94, 10),
    "C109": BKS(828.94, 10),

    # RC1 — mixed, short horizon (8 instances)
    "RC101": BKS(1623.58, 14),
    "RC102": BKS(1466.84, 12),
    "RC103": BKS(1261.67, 11),
    "RC104": BKS(1135.48, 10),
    "RC105": BKS(1518.60, 13),
    "RC106": BKS(1377.35, 11),
    "RC107": BKS(1212.83, 11),
    "RC108": BKS(1117.53, 10),

    # R2 — random, long horizon (11 instances)
    "R201": BKS(1252.37, 4),
    "R202": BKS(1191.70, 3),
    "R203": BKS( 939.50, 3),
    "R204": BKS( 825.52, 2),
    "R205": BKS( 994.42, 3),
    "R206": BKS( 906.14, 3),
    "R207": BKS( 890.61, 2),
    "R208": BKS( 726.82, 2),
    "R209": BKS( 909.16, 3),
    "R210": BKS( 939.34, 3),
    "R211": BKS( 892.71, 2),

    # C2 — clustered, long horizon (8 instances)
    "C201": BKS(591.56, 3),
    "C202": BKS(591.56, 3),
    "C203": BKS(591.17, 3),
    "C204": BKS(590.60, 3),
    "C205": BKS(588.88, 3),
    "C206": BKS(588.49, 3),
    "C207": BKS(588.29, 3),
    "C208": BKS(588.32, 3),

    # RC2 — mixed, long horizon (8 instances)
    "RC201": BKS(1406.94, 4),
    "RC202": BKS(1365.65, 3),
    "RC203": BKS(1049.62, 3),
    "RC204": BKS( 798.41, 3),
    "RC205": BKS(1297.19, 4),
    "RC206": BKS(1146.32, 3),
    "RC207": BKS(1061.14, 3),
    "RC208": BKS( 828.14, 3),
}

# Quick sanity-check: 12 + 9 + 8 + 11 + 8 + 8 = 56 instances.
assert len(SOLOMON_BKS) == 56, f"Expected 56 Solomon instances in BKS table, got {len(SOLOMON_BKS)}"
