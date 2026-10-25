"""Validate a bank configuration (and optionally a sample file) before deploying it.

    python -m recon.cli.validate_bank_config config/banks/icici.v2.yaml --sample icici_0315.csv

Exits 0 when the configuration is valid and every sampled row maps cleanly, 1 when it
is not, 2 on a usage error. Nothing is stored and nothing goes live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from recon.config.validation import ValidationReport, validate_candidate


def render(report: ValidationReport) -> str:
    lines = [f"configuration: {'OK' if not report.config_errors else 'INVALID'}"]
    if report.bank_code:
        lines.append(f"bank {report.bank_code}, version {report.config_version}")
    lines.extend(f"  error: {e}" for e in report.config_errors)
    if report.sample is not None:
        s = report.sample
        lines.append(f"sample header: {', '.join(s.header)}")
        lines.append(f"  {s.change.describe()}")
        lines.append(f"  rows checked {s.rows_checked}, valid {s.rows_valid}")
        lines.extend(f"  problem: {e}" for e in s.errors)
        lines.extend(f"  row {i + 1}: {row}" for i, row in enumerate(s.preview))
    lines.extend(f"warning: {w}" for w in report.warnings)
    lines.append("RESULT: " + ("PASS" if report.ok else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("config", type=Path, help="candidate bank configuration (YAML)")
    parser.add_argument("--sample", type=Path, help="a sample of the bank's CSV file")
    args = parser.parse_args(argv)
    try:
        config_text = args.config.read_text(encoding="utf-8")
        sample_text = args.sample.read_text(encoding="utf-8") if args.sample else None
    except OSError as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2
    report = validate_candidate(config_text, str(args.config), sample_text)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
