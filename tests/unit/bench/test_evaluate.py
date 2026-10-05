# tests/unit/bench/test_evaluate.py
from __future__ import annotations

import uuid
from datetime import date

from recon.bench.dataset import SyntheticRecord
from recon.bench.evaluate import MatchOutcome, evaluate


def _rec(source: str, entity: int, scenario: str, pair_key: int | None) -> SyntheticRecord:
    return SyntheticRecord(
        id=uuid.uuid4(), source=source, reference="ABC0000001", amount_minor=1,
        txn_date=date(2026, 3, 1), direction="CR", counterparty=None,
        entity_id=entity, scenario=scenario, pair_key=pair_key,
    )  # fmt: skip


def _world() -> tuple[list[SyntheticRecord], dict[str, SyntheticRecord]]:
    named = {
        "a_int": _rec("INTERNAL", 0, "exact", 0),
        "a_ext": _rec("EXTERNAL", 0, "exact", 0),
        "b_int": _rec("INTERNAL", 1, "typo_no_counterparty", 1),
        "b_ext": _rec("EXTERNAL", 1, "typo_no_counterparty", 1),
        "d_int": _rec("INTERNAL", 2, "sequential_decoy", None),
        "d_ext": _rec("EXTERNAL", 2, "sequential_decoy", None),
        "x_int": _rec("INTERNAL", 3, "internal_only", None),
    }  # fmt: skip
    return list(named.values()), named


def _o(
    a: SyntheticRecord, b: SyntheticRecord, status: str = "AUTO_MATCHED", kind: str = "EXACT"
) -> MatchOutcome:
    return MatchOutcome(a.id, b.id, status, kind)


def test_correct_matches_and_review_are_counted_separately() -> None:
    records, n = _world()
    ev = evaluate(
        records, [_o(n["a_int"], n["a_ext"]), _o(n["b_int"], n["b_ext"], "PENDING_REVIEW", "FUZZY")]
    )
    assert (ev.auto_total, ev.auto_correct, ev.false_auto_matches) == (1, 1, 0)
    assert (ev.review_total, ev.review_correct, ev.exact_correct) == (1, 1, 1)
    assert (ev.internal_count, ev.external_count, ev.true_pairs) == (4, 3, 2)
    assert ev.per_scenario["exact"] == {"auto_correct": 1}
    assert ev.per_scenario["typo_no_counterparty"] == {"review_correct": 1}
    assert ev.per_scenario["sequential_decoy"] == {"unmatched": 1}
    assert ev.per_scenario["internal_only"] == {"unmatched": 1}


def test_an_auto_match_between_two_decoys_is_a_false_match() -> None:
    records, n = _world()
    ev = evaluate(records, [_o(n["d_int"], n["d_ext"], kind="FUZZY")])
    assert (ev.auto_total, ev.auto_correct, ev.false_auto_matches) == (1, 0, 1)
    assert ev.auto_precision == 0.0
    assert ev.per_scenario["sequential_decoy"] == {"auto_wrong": 1}


def test_matching_the_wrong_partner_of_a_true_pair_is_wrong() -> None:
    records, n = _world()
    ev = evaluate(records, [_o(n["a_int"], n["b_ext"])])
    assert ev.false_auto_matches == 1
    assert ev.per_scenario["exact"] == {"auto_wrong": 1}


def test_rates_use_the_documented_denominators() -> None:
    records, n = _world()
    ev = evaluate(records, [_o(n["a_int"], n["a_ext"])])
    assert ev.exact_rate_of_externals == 1 / 3
    assert ev.exact_rate_of_true_pairs == 1 / 2
    assert ev.auto_recall_of_true_pairs == 1 / 2
    assert ev.auto_precision == 1.0


def test_no_outcomes_gives_none_precision_and_unmatched_everything() -> None:
    records, _ = _world()
    ev = evaluate(records, [])
    assert ev.auto_precision is None and ev.auto_total == 0
    assert ev.per_scenario["exact"] == {"unmatched": 1}
