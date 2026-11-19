"""Download Solomon's 56 VRPTW 100-customer benchmark instances.

Fetches each instance from a public github mirror and writes it to
backend/tests/validation_and_verification/data/solomon/<name>.txt.

Solomon's instances are public-domain academic benchmarks dating to
1987. The mirrors below redistribute them; we try multiple sources so a
single mirror going offline doesn't block V&V work. The first source
that yields a parseable file wins.

Usage:
    python backend/scripts/download_solomon.py            # download all 56
    python backend/scripts/download_solomon.py R101 C101  # download a subset
    python backend/scripts/download_solomon.py --check    # list missing only
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests.validation_and_verification._solomon_bks import SOLOMON_BKS  # noqa: E402
from tests.validation_and_verification._solomon_loader import parse_solomon_text  # noqa: E402

DATA_DIR = BACKEND_ROOT / "tests" / "validation_and_verification" / "data" / "solomon"

# Mirrors are tried in order. Each entry is a callable: instance_name -> URL.
# Both currently point to public github raw files of the iRB-Lab and
# ScorpJD py-ga-VRPTW repositories, which redistribute Solomon's instances
# at /data/text/<name>.txt. If a mirror disappears, see the SINTEF page or
# Solomon's original site (linked in data/solomon/README.md).
_MIRRORS: list = [
    lambda name: f"https://raw.githubusercontent.com/iRB-Lab/py-ga-VRPTW/master/data/text/{name}.txt",
    lambda name: f"https://raw.githubusercontent.com/ScorpJD/python-ga-VRPTW/master/data/text/{name}.txt",
]

_TIMEOUT_S = 15


def fetch_one(instance_name: str, *, force: bool = False) -> tuple[bool, str]:
    """Try each mirror until one returns parseable text.

    Returns (success, message).
    """
    out_path = DATA_DIR / f"{instance_name}.txt"
    if out_path.exists() and not force:
        return True, f"  {instance_name}: already present, skipping"

    last_err = "no mirrors tried"
    for mirror in _MIRRORS:
        url = mirror(instance_name)
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_err = f"{url}: {exc}"
            continue

        # Validate shape before writing — protects against HTML 404 pages
        # being saved as `R101.txt`.
        try:
            parse_solomon_text(text)
        except ValueError as exc:
            last_err = f"{url}: not a Solomon file ({exc})"
            continue

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
        return True, f"  {instance_name}: ok ({len(text)} bytes from {url.split('//')[1].split('/')[0]})"

    return False, f"  {instance_name}: FAILED — last error: {last_err}"


def list_missing() -> list[str]:
    """Return names of BKS-known instances without a vendored .txt."""
    return [
        name for name in SOLOMON_BKS
        if not (DATA_DIR / f"{name}.txt").exists()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "instances",
        nargs="*",
        help="Specific instance names to fetch (e.g., R101 C201). Default: all 56.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only list missing instances; don't download.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a file already exists.",
    )
    args = parser.parse_args()

    if args.check:
        missing = list_missing()
        if not missing:
            print(f"All 56 Solomon instances vendored at {DATA_DIR}")
            return 0
        print(f"{len(missing)}/56 missing from {DATA_DIR}:")
        for name in missing:
            print(f"  {name}")
        return 1

    targets = [n.upper() for n in args.instances] if args.instances else list(SOLOMON_BKS.keys())
    unknown = [n for n in targets if n not in SOLOMON_BKS]
    if unknown:
        print(f"Unknown instance(s): {unknown}", file=sys.stderr)
        print(f"Known: {sorted(SOLOMON_BKS.keys())}", file=sys.stderr)
        return 2

    print(f"Fetching {len(targets)} Solomon instance(s) → {DATA_DIR}")
    n_ok = 0
    n_fail = 0
    for name in targets:
        ok, msg = fetch_one(name, force=args.force)
        print(msg)
        if ok:
            n_ok += 1
        else:
            n_fail += 1

    print(f"\nDone: {n_ok} ok, {n_fail} failed")
    if n_fail > 0:
        print(
            "\nIf all mirrors failed, see "
            f"{DATA_DIR / 'README.md'} for manual sources.",
            file=sys.stderr,
        )
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
