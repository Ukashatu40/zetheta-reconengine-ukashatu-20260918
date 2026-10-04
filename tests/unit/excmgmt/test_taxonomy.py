# tests/unit/excmgmt/test_taxonomy.py
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from recon.domain.enums import EscalationTier, ExceptionCategory, ExceptionSeverity
from recon.excmgmt.taxonomy import (
    AutoResolvable,
    TaxonomyConfigError,
    default_taxonomy_path,
    load_taxonomy,
)

_PDF_SLA_MINUTES = {
    "MISSING_INTERNAL": 240, "MISSING_EXTERNAL": 240, "AMOUNT_MISMATCH": 120,
    "DUPLICATE_INTERNAL": 60, "DUPLICATE_EXTERNAL": 120, "DATE_MISMATCH": 480,
    "CURRENCY_MISMATCH": 240, "DIRECTION_REVERSAL": 30, "PARTIAL_MATCH": 240,
    "NETTED_SETTLEMENT": 480, "FEE_DEDUCTION": 120, "FX_VARIANCE": 240,
    "STALE_TRANSACTION": 1440, "FORMAT_ERROR": 60, "REFERENCE_TRUNCATED": 120,
    "TIMEZONE_OFFSET": 60, "REVERSAL_PENDING": 1440, "REGULATORY_HOLD": 2880,
}  # fmt: skip


def test_taxonomy_loads_with_every_category() -> None:
    taxonomy = load_taxonomy()
    assert {r.category for r in taxonomy.categories} == set(ExceptionCategory)
    assert len(taxonomy.categories) == 19  # PDF's 18 plus SETTLEMENT_DELAY


@pytest.mark.parametrize(("name", "minutes"), sorted(_PDF_SLA_MINUTES.items()))
def test_sla_matches_the_pdf_table(name: str, minutes: int) -> None:
    rule = load_taxonomy().rule_for(ExceptionCategory(name))
    assert rule.sla_minutes == minutes


def test_direction_reversal_applies_the_ae03_correction() -> None:
    rule = load_taxonomy().rule_for(ExceptionCategory.DIRECTION_REVERSAL)
    assert rule.sla_minutes == 30  # PDF says 60; AE-03 corrects it
    assert rule.default_tier is EscalationTier.TIER_4_COMPLIANCE
    assert rule.auto_resolvable is AutoResolvable.NEVER
    assert rule.base_severity is ExceptionSeverity.CRITICAL


def test_missing_category_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(default_taxonomy_path().read_text())
    data["categories"] = data["categories"][1:]
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(TaxonomyConfigError, match="missing"):
        load_taxonomy(path)


def test_duplicate_category_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(default_taxonomy_path().read_text())
    data["categories"].append(data["categories"][0])
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(TaxonomyConfigError, match="duplicated"):
        load_taxonomy(path)
