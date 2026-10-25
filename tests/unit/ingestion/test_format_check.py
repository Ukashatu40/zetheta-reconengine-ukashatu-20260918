"""FORMAT_CHANGE_DETECTED (B4.1): header comparison against the bank's mapping."""

from __future__ import annotations

import io

from recon.config.registry import default_bank_configs
from recon.ingestion.format_check import compare_header, expected_columns, read_header

_ICICI = default_bank_configs()["ICICI"]
_EXPECTED = ["Txn Ref No", "Txn Date", "Txn Amount", "Type", "Party Name", "Remarks"]


def test_the_expected_columns_come_from_the_mapping() -> None:
    assert expected_columns(_ICICI) == _EXPECTED


def test_a_matching_header_is_no_change() -> None:
    change = compare_header(_ICICI, _EXPECTED)
    assert (change.missing, change.unexpected, change.reordered) == ([], [], False)
    assert change.has_drift is False and change.describe() == "no change"


def test_reordered_columns_are_harmless_but_noted() -> None:
    change = compare_header(_ICICI, [*_EXPECTED[3:], *_EXPECTED[:3]])
    assert (change.reordered, change.blocks_ingestion, change.has_drift) == (True, False, True)
    assert change.describe() == "columns reordered"


def test_b41_new_columns_are_reported_but_do_not_block() -> None:
    header = [*_EXPECTED[:4], "Tax Deducted", "Net Amount", *_EXPECTED[4:]]
    change = compare_header(_ICICI, header)
    assert change.unexpected == ["Tax Deducted", "Net Amount"]
    assert change.blocks_ingestion is False and change.has_drift is True


def test_a_missing_column_blocks_ingestion() -> None:
    header = [c for c in _EXPECTED if c != "Txn Date"]
    change = compare_header(_ICICI, header)
    assert (change.missing, change.blocks_ingestion) == (["Txn Date"], True)
    assert "missing columns: Txn Date" in change.describe()


def test_a_renamed_column_is_one_missing_and_one_new() -> None:
    header = ["Transaction Ref" if c == "Txn Ref No" else c for c in _EXPECTED]
    change = compare_header(_ICICI, header)
    assert (change.missing, change.unexpected) == (["Txn Ref No"], ["Transaction Ref"])


def test_whitespace_around_header_names_is_ignored() -> None:
    assert compare_header(_ICICI, [f" {c} " for c in _EXPECTED]).has_drift is False


def test_a_bank_with_no_csv_section_expects_nothing() -> None:
    assert expected_columns(_ICICI.model_copy(update={"csv": None})) == []


def test_read_header_leaves_the_stream_where_it_started() -> None:
    stream = io.StringIO("a,b,c\n1,2,3\n")
    assert read_header(stream, _ICICI) == ["a", "b", "c"]
    assert stream.tell() == 0
