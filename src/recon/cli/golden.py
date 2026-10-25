"""Golden dataset tools.

    python -m recon.cli.golden --write-manifest          # refresh data/golden/manifest.json
    python -m recon.cli.golden --write-files data/samples # write the sample statement files
    python -m recon.cli.golden --write-files out --scale 50  # a larger volume, same planted cases

The manifest is committed; the golden test regenerates the data and fails if the
generator no longer produces it (so the manifest cannot silently go stale).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from recon.paths import config_dir
from recon.samples.generator import DEFAULT_SEED, build_sample_set


def manifest_path() -> Path:
    return config_dir().parent / "data" / "golden" / "manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the golden dataset and its manifest")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--scale", type=int, default=1, help="multiplies the high-volume classes")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--write-files", type=Path, default=None, metavar="DIR")
    args = parser.parse_args(argv)
    if not (args.write_manifest or args.write_files):
        parser.print_usage(sys.stderr)
        return 2

    sample = build_sample_set(args.seed, args.scale)
    if args.write_manifest:
        path = manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sample.manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {path}")
    if args.write_files:
        sample.write_to(args.write_files)
        print(f"wrote {len(sample.files)} files and the exchange-rate file to {args.write_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
