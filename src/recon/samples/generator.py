# src/recon/samples/generator.py
"""Deterministic sample and golden data (A8.1, A8.3, D10).

build_sample_set(seed, scale) returns statement files in every supported format plus
a MANIFEST that says, for every planted scenario, what the engine must do with it.
The same seed always yields byte-identical files, so a manifest committed to the
repository can gate every change: the golden test regenerates the data, runs the
real pipeline and fails on any deviation (A8.3).

Scenario classes, per A8.3 and Day 3/4:
  exact          reference, amount, currency and direction identical          (>= 100)
  fuzzy_auto     reference varies, still above the auto-match line
  tolerance      amount off by one paisa, within the +/-0.01 rule
  fuzzy_review   same reference, amount off: parked for review and named
  split/netted   one entry settled as several, several netted into one  (5 + 5, + B4.2)
  one scenario   for each of the 19 exception categories

`scale` multiplies the high-volume classes only; the planted exceptions stay fixed,
the way D10's files hold a fixed number of planted exceptions in a large file.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from recon.config.models import BankConfig
from recon.config.registry import load_bank_configs
from recon.samples.entries import Entry, write_camt, write_csv, write_mt940

GENERATOR_VERSION = 1
DEFAULT_SEED = 20260918
DEFAULT_ANCHOR = date(2026, 3, 12)  # a Thursday
AS_OF = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)  # the day after the default anchor, at noon UTC
_THURSDAY = 3

_FIRST = ("ACME", "BETA", "GAMMA", "DELTA", "OMEGA", "SIGMA", "ZENITH", "NOVA", "ORBIT", "PRIME")
_SECOND = ("TRADING", "LOGISTICS", "FOODS", "TEXTILES", "ENERGY", "SYSTEMS", "PHARMA", "MOTORS")
_SUFFIX = ("PVT LTD", "LLP", "INC", "CORP")


@dataclass(frozen=True, slots=True)
class SampleFile:
    name: str
    bank: str
    source: str  # INTERNAL or EXTERNAL
    format_type: str  # CSV, MT940 or CAMT053
    content: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    kind: str
    bank: str
    internal_refs: tuple[str, ...]
    external_refs: tuple[str, ...]
    # EXACT, FUZZY, TOLERANCE, SPLIT, NETTED, REVIEW, CONFIRMED, or None for "left unmatched"
    expect_match: str | None
    expect_exceptions: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "bank": self.bank,
            "internal_refs": list(self.internal_refs),
            "external_refs": list(self.external_refs),
            "expect_match": self.expect_match,
            "expect_exceptions": sorted(self.expect_exceptions),
            "note": self.note,
        }


@dataclass(slots=True)
class SampleSet:
    seed: int
    scale: int
    anchor: date = DEFAULT_ANCHOR
    files: list[SampleFile] = field(default_factory=list)
    fx_rates_csv: str = ""
    scenarios: list[Scenario] = field(default_factory=list)

    @property
    def as_of(self) -> datetime:
        """The moment the data is reconciled: noon UTC the day after the anchor."""
        day = self.anchor + timedelta(days=1)
        return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)

    @property
    def fx_file_name(self) -> str:
        start, end = self.anchor - timedelta(days=10), self.anchor + timedelta(days=1)
        return f"fx_rates_{start:%Y%m%d}_{end:%Y%m%d}.csv"

    def manifest(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for s in self.scenarios:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        categories = sorted({c for s in self.scenarios for c in s.expect_exceptions})
        return {
            "generator_version": GENERATOR_VERSION,
            "seed": self.seed,
            "scale": self.scale,
            "anchor": self.anchor.isoformat(),
            "as_of": self.as_of.isoformat(),
            "files": [
                {
                    "name": f.name,
                    "bank": f.bank,
                    "source": f.source,
                    "format": f.format_type,
                    "sha256": f.sha256,
                }
                for f in self.files
            ],
            "fx_rates_sha256": hashlib.sha256(self.fx_rates_csv.encode()).hexdigest(),
            "scenario_counts": dict(sorted(by_kind.items())),
            "exception_categories_planted": categories,
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def write_to(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for f in self.files:
            (directory / f.name).write_text(f.content, encoding="utf-8")
        (directory / "fx_rates_20260302_20260313.csv").write_text(
            self.fx_rates_csv, encoding="utf-8"
        )


class _Builder:
    """Accumulates entries per output file and the scenarios that explain them."""

    def __init__(self, seed: int, scale: int, anchor: date) -> None:
        self.rng = random.Random(seed)
        self.scale = scale
        self.thu = anchor  # the booking day of nearly everything
        self.late = anchor + timedelta(days=6)  # the following Wednesday: four business days on
        self.old = anchor - timedelta(days=10)  # well outside the one-day reconciliation window
        self.banks: dict[str, BankConfig] = load_bank_configs()
        self.entries: dict[tuple[str, str], list[Entry]] = {}
        self.scenarios: list[Scenario] = []
        self.corrupt: dict[tuple[str, str], set[int]] = {}
        self._counters: dict[str, int] = {}

    # -- helpers --------------------------------------------------------------------------

    def ref(self, prefix: str) -> str:
        n = self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{n:07d}"

    def amount(self, low: int = 500_00, high: int = 5_000_000_00) -> int:
        return self.rng.randrange(low, high, 100) + self.rng.choice((0, 0, 25, 50, 75))

    def name(self) -> str:
        return f"{self.rng.choice(_FIRST)} {self.rng.choice(_SECOND)} {self.rng.choice(_SUFFIX)}"

    def put(self, bank: str, slot: str, *entries: Entry) -> list[int]:
        """Adds entries to a file slot (e.g. 'internal', 'csv', 'mt940', 'camt'); returns
        their 1-based line numbers."""
        rows = self.entries.setdefault((bank, slot), [])
        lines = []
        for e in entries:
            rows.append(e)
            lines.append(len(rows))
        return lines

    def add(self, scenario: Scenario) -> None:
        self.scenarios.append(scenario)

    # -- scenario families ---------------------------------------------------------------------

    def pairs(self, bank: str, ext_slot: str, prefix: str, count: int, kind: str) -> None:
        for _ in range(count):
            r, amount, party = self.ref(prefix), self.amount(), self.name()
            day = self.thu
            exc: tuple[str, ...] = ()
            ext_ref, ext_amount, expect = r, amount, "EXACT"
            if kind == "fuzzy_auto":
                ext_ref, expect = r + "A", "FUZZY"
            elif kind == "tolerance":
                ext_amount, expect = amount + 1, "TOLERANCE"
            elif kind == "fuzzy_review":
                ext_amount, expect, exc = amount + 300, "REVIEW", ("AMOUNT_MISMATCH",)
            cp = None if ext_slot in ("mt940", "camt") else party
            self.put(bank, "internal", Entry(r, day, amount, "CR", party, f"Settlement {r}"))
            self.put(
                bank, ext_slot, Entry(ext_ref, day, ext_amount, "CR", cp, f"Credit received {r}")
            )
            self.add(Scenario(f"{kind}:{r}", kind, bank, (r,), (ext_ref,), expect, exc))

    def split_and_netted(self, bank: str, prefix: str, count: int) -> None:
        for _ in range(count):
            r, party = self.ref(f"SPL{prefix}"), self.name()
            parts = [self.amount(1_000_00, 200_000_00) for _ in range(3)]
            self.put(bank, "internal", Entry(r, self.thu, sum(parts), "CR", party, f"Order {r}"))
            ext = [
                Entry(f"{r}-{i}", self.thu, p, "CR", party, f"Part {i} of {r}")
                for i, p in enumerate(parts, 1)
            ]
            self.put(bank, "csv", *ext)
            self.add(
                Scenario(
                    f"split:{r}",
                    "split",
                    bank,
                    (r,),
                    tuple(e.ref for e in ext),
                    "SPLIT",
                    ("PARTIAL_MATCH",),
                )
            )
        for _ in range(count):
            r, party = self.ref(f"NET{prefix}"), self.name()
            parts = [self.amount(1_000_00, 200_000_00) for _ in range(4)]
            ints = [
                Entry(f"{r}-{i:02d}", self.thu, p, "CR", party, f"Item {i} of {r}")
                for i, p in enumerate(parts, 1)
            ]
            self.put(bank, "internal", *ints)
            self.put(
                bank, "csv", Entry(r, self.thu, sum(parts), "CR", party, f"Net settlement {r}")
            )
            self.add(
                Scenario(
                    f"netted:{r}",
                    "netted",
                    bank,
                    tuple(e.ref for e in ints),
                    (r,),
                    "NETTED",
                    ("NETTED_SETTLEMENT",),
                )
            )

    def singles(self) -> None:
        """One scenario per exception category (plus MISSING and friends), each fully specified."""
        hdfc = "HDFC"
        party = self.name()

        r = self.ref("ZMI")  # MISSING_INTERNAL: the bank has it, the ledger does not
        party = self.name()
        self.put(hdfc, "csv", Entry(r, self.thu, self.amount(), "CR", party, "Unknown credit"))
        self.add(
            Scenario(
                f"cat:MISSING_INTERNAL:{r}", "category", hdfc, (), (r,), None, ("MISSING_INTERNAL",)
            )
        )

        r = self.ref("QME")  # MISSING_EXTERNAL: the ledger has it, the bank does not (yet)
        party = self.name()
        self.put(
            hdfc, "internal", Entry(r, self.thu, self.amount(), "CR", party, "Awaiting settlement")
        )
        self.add(
            Scenario(
                f"cat:MISSING_EXTERNAL:{r}", "category", hdfc, (r,), (), None, ("MISSING_EXTERNAL",)
            )
        )

        r = self.ref("VST")  # STALE_TRANSACTION: older than the 1-day window and still unmatched
        party = self.name()
        self.put(
            hdfc, "internal", Entry(r, self.old, self.amount(), "CR", party, "Old and unsettled")
        )
        self.add(
            Scenario(
                f"cat:STALE_TRANSACTION:{r}",
                "category",
                hdfc,
                (r,),
                (),
                None,
                ("STALE_TRANSACTION",),
            )
        )

        r = self.ref("UDR")  # DIRECTION_REVERSAL: credit in the ledger, debit at the bank
        party = self.name()
        amount = self.amount()
        self.put(hdfc, "internal", Entry(r, self.thu, amount, "CR", party, "Customer payment"))
        self.put(hdfc, "csv", Entry(r, self.thu, amount, "DR", party, "Customer payment"))
        self.add(
            Scenario(
                f"cat:DIRECTION_REVERSAL:{r}",
                "category",
                hdfc,
                (r,),
                (r,),
                None,
                ("DIRECTION_REVERSAL",),
            )
        )

        r = self.ref("WDI")  # DUPLICATE_INTERNAL: the ledger recorded it twice
        party = self.name()
        amount = self.amount()
        self.put(hdfc, "internal", *[Entry(r, self.thu, amount, "CR", party, "Posted twice")] * 2)
        self.put(hdfc, "csv", Entry(r, self.thu, amount, "CR", party, "Posted twice"))
        self.add(
            Scenario(
                f"cat:DUPLICATE_INTERNAL:{r}",
                "category",
                hdfc,
                (r,),
                (r,),
                "EXACT",
                ("DUPLICATE_INTERNAL",),
            )
        )

        r = self.ref("XDE")  # DUPLICATE_EXTERNAL: the bank listed it twice
        party = self.name()
        amount = self.amount()
        self.put(hdfc, "internal", Entry(r, self.thu, amount, "CR", party, "Listed twice"))
        self.put(hdfc, "csv", *[Entry(r, self.thu, amount, "CR", party, "Listed twice")] * 2)
        self.add(
            Scenario(
                f"cat:DUPLICATE_EXTERNAL:{r}",
                "category",
                hdfc,
                (r,),
                (r,),
                "EXACT",
                ("DUPLICATE_EXTERNAL",),
            )
        )

        self._amount_and_date_categories()
        self._time_and_currency_categories()
        self._fx_scenarios()

    def _amount_and_date_categories(self) -> None:
        hdfc = "HDFC"
        party = self.name()

        r = self.ref("TFE")  # FEE_DEDUCTION: HDFC's fee is 500 + 0.10%; 100000 - 99400 = 600
        party = self.name()
        self.put(hdfc, "internal", Entry(r, self.thu, 100_000, "CR", party, "Merchant settlement"))
        self.put(
            hdfc, "csv", Entry(r, self.thu, 99_400, "CR", party, "Merchant settlement net of fee")
        )
        self.add(
            Scenario(
                f"cat:FEE_DEDUCTION:{r}",
                "category",
                hdfc,
                (r,),
                (r,),
                "CONFIRMED",
                ("FEE_DEDUCTION",),
            )
        )

        r = self.ref("SDM")  # DATE_MISMATCH: booked four business days apart, otherwise identical
        party = self.name()
        amount = self.amount()
        self.put(hdfc, "internal", Entry(r, self.thu, amount, "CR", party, "Late booking"))
        self.put(hdfc, "csv", Entry(r, self.late, amount, "CR", party, "Late booking"))
        self.add(
            Scenario(
                f"cat:DATE_MISMATCH:{r}",
                "category",
                hdfc,
                (r,),
                (r,),
                "EXACT",
                ("DATE_MISMATCH", "SETTLEMENT_DELAY"),
            )
        )

        long_ref = "TRUNCATEDREF" + "0" * 4 + "ABCD"  # 20 characters; the bank keeps 16
        amount = self.amount()
        self.put(hdfc, "internal", Entry(long_ref, self.thu, amount, "CR", party, "Long reference"))
        self.put(hdfc, "csv", Entry(long_ref[:16], self.thu, amount, "CR", party, "Long reference"))
        self.add(
            Scenario(
                f"cat:REFERENCE_TRUNCATED:{long_ref}",
                "category",
                hdfc,
                (long_ref,),
                (long_ref[:16],),
                "FUZZY",
                ("REFERENCE_TRUNCATED",),
            )
        )

        r = self.ref("RRP")  # REVERSAL_PENDING: a reversal posted internally, not yet at the bank
        party = self.name()
        self.put(
            hdfc,
            "internal",
            Entry(r, self.thu, self.amount(), "DR", party, f"REVERSAL of payment {r}"),
        )
        self.add(
            Scenario(
                f"cat:REVERSAL_PENDING:{r}", "category", hdfc, (r,), (), None, ("REVERSAL_PENDING",)
            )
        )

        r = self.ref("PHD")  # REGULATORY_HOLD: the bank marked it for AML review
        party = self.name()
        self.put(
            hdfc, "csv", Entry(r, self.thu, self.amount(), "CR", party, "AML HOLD placed by bank")
        )
        self.add(
            Scenario(
                f"cat:REGULATORY_HOLD:{r}", "category", hdfc, (), (r,), None, ("REGULATORY_HOLD",)
            )
        )

        r = self.ref("BFE")  # FORMAT_ERROR: a corrupt date in the bank's file
        party = self.name()
        lines = self.put(hdfc, "csv", Entry(r, self.thu, self.amount(), "CR", party, "Corrupt row"))
        self.corrupt.setdefault((hdfc, "csv"), set()).update(lines)
        self.add(
            Scenario(f"cat:FORMAT_ERROR:{r}", "category", hdfc, (), (r,), None, ("FORMAT_ERROR",))
        )

    def _time_and_currency_categories(self) -> None:
        party = self.name()
        yes = "YESB"  # TIMEZONE_OFFSET: 23:40 and 00:15 are on different dates but 35 minutes apart
        r = self.ref("TZO")
        party = self.name()
        amount = self.amount()
        self.put(
            yes,
            "internal",
            Entry(r, self.thu, amount, "CR", party, "Late night payment", time="23:40"),
        )
        self.put(
            yes,
            "csv",
            Entry(
                r,
                self.thu + timedelta(days=1),
                amount,
                "CR",
                party,
                "Late night payment",
                time="00:15",
            ),
        )
        self.add(
            Scenario(
                f"cat:TIMEZONE_OFFSET:{r}",
                "category",
                yes,
                (r,),
                (r,),
                "EXACT",
                ("TIMEZONE_OFFSET",),
            )
        )

        kotak = "KOTAK"  # SETTLEMENT_DELAY: booked Thursday, value-dated six days later
        r = self.ref("KSD")
        party = self.name()
        amount = self.amount()
        self.put(kotak, "internal", Entry(r, self.thu, amount, "CR", party, "Slow settlement"))
        self.put(
            kotak,
            "camt",
            Entry(r, self.thu, amount, "CR", party, "Slow settlement", settle_day=self.late),
        )
        self.add(
            Scenario(
                f"cat:SETTLEMENT_DELAY:{r}",
                "category",
                kotak,
                (r,),
                (r,),
                "EXACT",
                ("SETTLEMENT_DELAY",),
            )
        )

        r = self.ref("CCM")  # CURRENCY_MISMATCH: SGD has no rate in the rate file
        party = self.name()
        self.put(
            kotak,
            "internal",
            Entry(r, self.thu, 100_000_00, "CR", party, "SGD payment booked in INR"),
        )
        self.put(
            kotak,
            "camt_sgd",
            Entry(r, self.thu, 1_700_00, "CR", party, "SGD payment", currency="SGD"),
        )
        self.add(
            Scenario(
                f"cat:CURRENCY_MISMATCH:{r}",
                "category",
                kotak,
                (r,),
                (r,),
                None,
                ("CURRENCY_MISMATCH",),
            )
        )

        r = self.ref("FXV")  # FX_VARIANCE: USD 1,300 at 83.40 is INR 108,420, 8% over INR 100,000
        party = self.name()
        self.put(kotak, "internal", Entry(r, self.thu, 100_000_00, "CR", party, "USD invoice"))
        self.put(
            kotak,
            "camt_usd",
            Entry(r, self.thu, 1_300_00, "CR", party, "USD invoice", currency="USD"),
        )
        self.add(
            Scenario(f"cat:FX_VARIANCE:{r}", "category", kotak, (r,), (r,), None, ("FX_VARIANCE",))
        )

    def _fx_scenarios(self) -> None:
        party = self.name()
        kotak = "KOTAK"
        r = self.ref("FXK")  # a cross-currency pair inside the 1.5% tolerance matches
        party = self.name()
        self.put(
            kotak,
            "internal",
            Entry(r, self.thu, 100_080_00, "CR", party, "USD invoice in tolerance"),
        )
        self.put(
            kotak,
            "camt_usd",
            Entry(r, self.thu, 1_200_00, "CR", party, "USD invoice", currency="USD"),
        )
        self.add(Scenario(f"fx:{r}", "cross_currency", kotak, (r,), (r,), "CROSS_CURRENCY"))

        # B4.2, corrected figures: GBP 142,857.14 netted at 84.00 is INR 1,19,99,999.76
        party = self.name()
        total = 1_199_999_976
        base, extra = divmod(total, 23)
        ints = [
            Entry(
                f"B42N-{i:03d}",
                self.thu,
                base + (1 if i <= extra else 0),
                "CR",
                party,
                f"Order {i}",
            )
            for i in range(1, 24)
        ]
        self.put(kotak, "internal", *ints)
        self.put(
            kotak,
            "camt_gbp",
            Entry("B42N", self.thu, 14_285_714, "CR", party, "Netted settlement", currency="GBP"),
        )
        self.add(
            Scenario(
                "b42:corrected",
                "netted_cross_currency",
                kotak,
                tuple(e.ref for e in ints),
                ("B42N",),
                "NETTED",
                ("NETTED_SETTLEMENT",),
                "AE-32 corrected figures",
            )
        )

        party = self.name()
        # B4.2 as the PDF writes it: INR 1.5 crore against the same GBP amount is a 25.8% variance
        self.put(
            kotak,
            "internal",
            Entry("B42W", self.thu, 1_500_000_000, "CR", party, "Written-up netting"),
        )
        self.put(
            kotak,
            "camt_gbp",
            Entry("B42W", self.thu, 14_285_714, "CR", party, "Netted settlement", currency="GBP"),
        )
        self.add(
            Scenario(
                "b42:as-written",
                "fx_variance_as_written",
                kotak,
                ("B42W",),
                ("B42W",),
                None,
                ("FX_VARIANCE",),
                "AE-32 as written",
            )
        )


def _fx_rates(anchor: date) -> str:
    """D10's rate file: 12 days x 6 currencies, RBI source, three deliberately wrong rates."""
    base = {
        "USD": "83.40",
        "GBP": "83.47",
        "EUR": "90.10",
        "JPY": "0.55",
        "AED": "22.70",
        "CHF": "93.20",
    }
    lines = ["date,currency,rate,source"]
    for offset in range(12):
        day = anchor - timedelta(days=10) + timedelta(days=offset)
        for currency, rate in base.items():
            value = rate
            if (currency, offset) in {("EUR", 3), ("CHF", 6), ("AED", 9)}:
                value = {"EUR": "99.90", "CHF": "129.00", "AED": "31.50"}[
                    currency
                ]  # the planted errors
            lines.append(f"{day.isoformat()},{currency},{value},RBI")
    return "\n".join(lines) + "\n"


def build_sample_set(
    seed: int = DEFAULT_SEED, scale: int = 1, anchor: date = DEFAULT_ANCHOR
) -> SampleSet:
    if scale < 1:
        raise ValueError("scale must be at least 1")
    if anchor.weekday() != _THURSDAY:
        raise ValueError("anchor must be a Thursday: the planted date cases rely on the calendar")
    b = _Builder(seed, scale, anchor)

    b.pairs("HDFC", "csv", "HDF", 40 * scale, "exact")
    b.pairs("HDFC", "csv", "HDG", 15 * scale, "fuzzy_auto")
    b.pairs("HDFC", "csv", "HDT", 5 * scale, "tolerance")
    b.pairs("HDFC", "csv", "HDR", 10 * scale, "fuzzy_review")
    b.pairs("ICICI", "csv", "ICI", 25 * scale, "exact")
    b.pairs("ICICI", "csv", "ICG", 10 * scale, "fuzzy_auto")
    b.pairs("ICICI", "csv", "ICR", 10 * scale, "fuzzy_review")
    b.pairs("AXIS", "mt940", "AXS", 20 * scale, "exact")
    b.pairs("SBI", "mt940", "SBI", 10 * scale, "exact")
    b.pairs("KOTAK", "camt", "KTK", 10 * scale, "exact")
    b.pairs("YESB", "csv", "YES", 5 * scale, "exact")
    b.pairs("INDB", "csv", "IND", 5 * scale, "exact")
    b.split_and_netted("HDFC", "H", 5)
    b.singles()

    result = SampleSet(seed, scale, anchor, scenarios=b.scenarios, fx_rates_csv=_fx_rates(anchor))
    result.files = _files(b, anchor)
    return result


# (bank, slot) -> (file name, source, format, writer kind)
_PLAN: dict[tuple[str, str], tuple[str, str, str]] = {
    ("HDFC", "internal"): ("internal_ledger_hdfc_{d}.csv", "INTERNAL", "CSV"),
    ("HDFC", "csv"): ("hdfc_settlement_{d}.csv", "EXTERNAL", "CSV"),
    ("ICICI", "internal"): ("internal_ledger_icici_{d}.csv", "INTERNAL", "CSV"),
    ("ICICI", "csv"): ("icici_settlement_{d}.csv", "EXTERNAL", "CSV"),
    ("AXIS", "internal"): ("internal_ledger_axis_{d}.csv", "INTERNAL", "CSV"),
    ("AXIS", "mt940"): ("axis_mt940_{d}.sta", "EXTERNAL", "MT940"),
    ("SBI", "internal"): ("internal_ledger_sbi_{d}.csv", "INTERNAL", "CSV"),
    ("SBI", "mt940"): ("sbi_mt940_{d}.sta", "EXTERNAL", "MT940"),
    ("KOTAK", "internal"): ("internal_ledger_kotak_{d}.csv", "INTERNAL", "CSV"),
    ("KOTAK", "camt"): ("kotak_camt053_{d}.xml", "EXTERNAL", "CAMT053"),
    ("KOTAK", "camt_usd"): ("kotak_camt053_usd_{d}.xml", "EXTERNAL", "CAMT053"),
    ("KOTAK", "camt_gbp"): ("kotak_camt053_gbp_{d}.xml", "EXTERNAL", "CAMT053"),
    ("KOTAK", "camt_sgd"): ("kotak_camt053_sgd_{d}.xml", "EXTERNAL", "CAMT053"),
    ("YESB", "internal"): ("internal_ledger_yesb_{d}.csv", "INTERNAL", "CSV"),
    ("YESB", "csv"): ("yesb_settlement_{d}.csv", "EXTERNAL", "CSV"),
    ("INDB", "internal"): ("internal_ledger_indb_{d}.csv", "INTERNAL", "CSV"),
    ("INDB", "csv"): ("indb_settlement_{d}.csv", "EXTERNAL", "CSV"),
}
_CAMT_CURRENCY = {"camt": "INR", "camt_usd": "USD", "camt_gbp": "GBP", "camt_sgd": "SGD"}


def _files(b: _Builder, anchor: date) -> list[SampleFile]:
    files: list[SampleFile] = []
    for (bank, slot), (name, source, fmt) in _PLAN.items():
        entries = b.entries.get((bank, slot))
        if not entries:
            continue
        config = b.banks[bank]
        if fmt == "CSV":
            content = write_csv(
                config, entries, corrupt_lines=sorted(b.corrupt.get((bank, slot), ()))
            )
        elif fmt == "MT940":
            assert config.mt940 is not None
            content = write_mt940(
                entries,
                decimal_separator=config.mt940.decimal_separator,
                account=f"{bank}0001234567",
                statement_no="00001/001",
            )
        else:
            content = write_camt(
                entries,
                currency=_CAMT_CURRENCY[slot],
                account=f"IN00{bank}1234567890",
                statement_id=f"STMT-{slot}",
            )
        files.append(SampleFile(name.format(d=f"{anchor:%Y%m%d}"), bank, source, fmt, content))
    return files
