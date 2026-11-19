from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the FastAPI OpenAPI schema to a checked-in JSON artifact.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("shared/openapi.generated.json"),
        help="Path to the output JSON file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.config import settings

    settings.SKIP_DB_STARTUP_CHECKS = True

    from app.main import app

    schema = app.openapi()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"exported OpenAPI schema to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
