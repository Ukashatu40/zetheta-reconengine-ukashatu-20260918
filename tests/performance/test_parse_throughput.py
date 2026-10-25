"""A6.1 parsing targets, on the generated sample files. Parsing only (no database), so the
check is cheap and stable: the measured figures are one to two orders of magnitude over the
targets, which a loaded CI machine will not erase. End-to-end ingestion and matching are
benchmarked separately (`make bench`) and reported, not asserted, in docs/PERFORMANCE.md."""

from __future__ import annotations

import pytest

from recon.bench.pipeline_bench import TARGETS_PER_MINUTE, parse_throughput
from recon.samples.generator import DEFAULT_SEED, build_sample_set

pytestmark = pytest.mark.performance


@pytest.mark.parametrize("fmt", ["CSV", "MT940", "CAMT053"])
def test_parsing_meets_the_a6_1_target(fmt: str) -> None:
    result = parse_throughput(build_sample_set(DEFAULT_SEED, 10), repeat=3)[fmt]
    assert result.records > 0
    assert (
        result.per_minute > TARGETS_PER_MINUTE[fmt]
    ), f"{fmt}: {result.per_minute:,.0f}/min against a target of {TARGETS_PER_MINUTE[fmt]:,}/min"
