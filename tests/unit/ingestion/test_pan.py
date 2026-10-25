"""Card number detection and masking (PCI DSS requirement 3, Case Study C6, AE-26)."""

from __future__ import annotations

import pytest

from recon.ingestion.pan import luhn_valid, mask_mapping, mask_pans

# Well-known test numbers: Visa, Mastercard, Amex, RuPay, Discover.
_PANS = [
    "4111111111111111",
    "5500005555555559",
    "378282246310005",
    "6521234567890123",  # RuPay-range prefix 65 (Luhn-corrected below in the test)
    "6011111111111117",
]


def _luhn_fix(prefix: str, length: int) -> str:
    body = prefix.ljust(length - 1, "0")
    for check in range(10):
        if luhn_valid(body + str(check)):
            return body + str(check)
    raise AssertionError("unreachable")


@pytest.mark.parametrize(
    "pan", ["4111111111111111", "5500005555555559", "378282246310005", "6011111111111117"]
)
def test_a_known_card_number_keeps_only_its_last_four_digits(pan: str) -> None:
    result = mask_pans(f"paid by card {pan} thanks")
    assert result.masked_count == 1
    assert pan not in result.text
    assert result.text == f"paid by card {'*' * (len(pan) - 4)}{pan[-4:]} thanks"


def test_groups_separated_by_spaces_or_hyphens_are_found() -> None:
    assert mask_pans("4111 1111 1111 1111").text == "************1111"
    assert mask_pans("4111-1111-1111-1111").text == "************1111"


def test_a_rupay_style_number_is_masked() -> None:
    pan = _luhn_fix("6521", 16)
    assert mask_pans(pan).masked_count == 1


def test_several_numbers_in_one_text_are_all_masked() -> None:
    result = mask_pans("4111111111111111 and 5500005555555559")
    assert result.masked_count == 2
    assert "4111111111111111" not in result.text and "5500005555555559" not in result.text


def test_a_number_that_fails_the_luhn_check_is_left_alone() -> None:
    assert mask_pans("4111111111111112").masked_count == 0  # off by one in the check digit


def test_a_number_without_a_card_network_prefix_is_left_alone() -> None:
    not_a_card = _luhn_fix("9999", 16)
    assert luhn_valid(not_a_card)
    assert mask_pans(not_a_card).masked_count == 0


@pytest.mark.parametrize(
    "text",
    [
        "UTR 123456789012",  # a 12-digit UPI/IMPS reference
        "SBIN0123456789012",  # NEFT UTR with letters
        "ref 20260315123456",  # a 14-digit timestamp-like reference with no card prefix
        "amount 1234567.89",
        "acct 0012345678901234567890",  # longer than any PAN
        "",
    ],
)
def test_ordinary_references_and_amounts_are_not_mangled(text: str) -> None:
    result = mask_pans(text)
    assert (result.text, result.masked_count) == (text, 0)


def test_a_valid_looking_prefix_inside_a_longer_digit_run_is_not_masked() -> None:
    """20 digits is longer than any card number: the leading 16 must not be carved out."""
    run = "4111111111111111" + "1111"
    assert mask_pans(run).masked_count == 0


def test_luhn_matches_the_standard_examples() -> None:
    assert luhn_valid("79927398713")
    assert not luhn_valid("79927398710")


def test_mask_mapping_masks_every_value_and_counts_them() -> None:
    masked, count = mask_mapping(
        {"reference": "REF1", "narration": "card 4111111111111111", "x": "5500005555555559"}
    )
    assert count == 2
    assert masked["reference"] == "REF1"
    assert masked["narration"] == "card ************1111"
    assert masked["x"] == "************5559"
