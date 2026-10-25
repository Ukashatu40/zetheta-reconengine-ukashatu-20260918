"""Writes the engine's OpenAPI schema to frontend/openapi.json, from which the dashboard's
TypeScript types are generated (`npm run gen:api` in frontend/).

    python scripts/export_openapi.py            # rewrite the snapshot
    python scripts/export_openapi.py --check    # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from recon.api.main import app

_OUT = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = _OUT.read_text(encoding="utf-8") if _OUT.exists() else ""
        if current != text:
            sys.stderr.write(
                "frontend/openapi.json is out of date; run scripts/export_openapi.py "
                "and `npm run gen:api` in frontend/\n"
            )
            return 1
        return 0
    _OUT.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {_OUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
