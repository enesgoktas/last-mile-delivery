# Solomon VRPTW instances — vendored benchmark data

This directory holds Solomon (1987) 100-customer VRPTW `.txt` instance
files. The 56-instance suite covers six families (R1, R2, C1, C2, RC1,
RC2) and is the standard benchmark for VRPTW solvers in the literature.

## How to populate

The simplest path:

```bash
cd backend && python scripts/download_solomon.py
```

That script fetches all 56 instances from a public mirror and writes
them as `R101.txt`, `C101.txt`, … into this directory.

## Manual sources (if the download script fails)

Any of the following is canonical:

- **SINTEF VRPTW page** — https://www.sintef.no/projectweb/top/vrptw/100-customers/
  (look for the "Download" link; the bundle is `solomon100.zip`)
- **VRP-REP** — https://www.vrp-rep.org/datasets/item/2014-0001.html
- **W. Solomon's original page** — https://web.cba.neu.edu/~msolomon/problems.htm
  (occasionally offline; oldest source)

After downloading, extract the `.txt` files into this directory. Filenames
must be the instance names: `R101.txt`, `C201.txt`, `RC108.txt`, etc.
Case-insensitive.

## File format

Plain text. The first non-empty line is the instance name. Then a
`VEHICLE` block (number + capacity), then a `CUSTOMER` block listing the
depot at customer 0 followed by 100 customers, each with x/y coordinates,
demand, ready time, due date, and service time.

Travel time is Euclidean distance with speed = 1, so a single number
captures both distance and time-to-traverse.

## What the V&V suite does with this data

`test_solomon.py` parses each vendored instance, runs rich_alns at a
60-second budget, and asserts:

1. The plan is feasibility-valid (independent validator returns 0 errors).
2. Total distance is within 30% of the published Best-Known Solution (BKS).
3. Across the full vendored set, mean gap is ≤ 10% (the headline V&V claim).

The BKS values come from Vidal et al. (2013) and the SINTEF reference
table; see `_solomon_bks.py`.

## Why this directory is empty in git

The `.txt` files are not committed (`.gitignore` excludes `*.txt` here)
to keep the repo small and to avoid copyright ambiguity even though the
instances are 30+ years old and academically public. Run the download
script after cloning.
