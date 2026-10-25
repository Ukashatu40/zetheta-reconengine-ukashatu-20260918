# src/recon/matching/strategies/group.py
"""Split and netted matching (A3.3), including cross-currency netting (B4.2).

SPLIT   one internal transaction settled as several external entries.
NETTED  several internal transactions settled as one external entry.

A group is only proposed among transactions that are LINKED, never among every
transaction that happens to fit: with enough candidates some subset always sums
to the target by coincidence. Two links count, strongest first:
  1. a shared reference stem ("PAY-100" for PAY-100-1, PAY-100-2, and the
     whole reference of the single side), taken from the original reference;
  2. the same counterparty, with dates within the window.
Direction must match and dates must be within the window of the single side.

Within a linked candidate set the whole set is tried first (the usual case: a
batch settles in full), then the bounded subset-sum DP. A group is claimed only
if the solution is unique; ambiguous and too-complex cases are counted and left
for a human, never guessed.

Same-currency sums must agree within tolerance_minor. When currencies differ
every leg is converted to INR at the rate for its own booking date and the
sums must agree within the bank's FX tolerance, A3.3's |a-b|/mean (B4.2). The
rate applied, the implied rate, and both INR figures go into the match
rationale and the audit entry.

Every member pairing becomes one match_results row sharing a match_group_id, so
the existing one-row-per-pair readers keep working. The single side's claim is
attached to the first row. All claims are taken atomically.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.fx.convert import implied_rate, relative_difference_percent, to_inr_minor
from recon.fx.service import FxQuote, FxService
from recon.matching.blocking.buckets import counterparty_key
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.subset_sum import SubsetOutcome, find_unique_subset
from recon.persistence.models import MatchResult, NormalisedTransaction
from recon.persistence.repositories.matching import MatchingRepository

_STEM_SEPARATORS = re.compile(r"[-_/.]")
_MAX_STEM_SUFFIX = 6
_CONFIDENCE_STEM = Decimal("0.950")
_CONFIDENCE_COUNTERPARTY = Decimal("0.900")
_CONFIDENCE_FX = Decimal("0.900")
_TWO = Decimal(2)
_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class GroupMatchConfig:
    max_split_members: int = 10
    max_netted_members: int = 50
    max_search_candidates: int = 24
    max_dp_states: int = 200_000
    date_window_days: int = 2
    tolerance_minor: int = 1
    fx_tolerance_percent: Decimal = Decimal("1.5")
    counterparty_prefix_length: int = 8
    min_stem_length: int = 4


@dataclass(slots=True)
class GroupMatchOutcome:
    split_groups: int = 0
    netted_groups: int = 0
    ambiguous_count: int = 0
    too_complex_count: int = 0
    missing_fx_count: int = 0
    skipped_claim_conflict_count: int = 0
    match_group_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def group_count(self) -> int:
        return self.split_groups + self.netted_groups


@dataclass(frozen=True, slots=True)
class _Solution:
    members: list[NormalisedTransaction]
    confidence: Decimal
    link: str
    fx: dict[str, Any] | None
    single_minor: int
    members_minor: int


def reference_stem(original_reference: str | None, min_length: int) -> str | None:
    """The reference without its trailing sequence component: PAY-100-2 -> PAY-100.
    None when there is no separator, the suffix is long, or the stem is too short."""
    if not original_reference:
        return None
    text = original_reference.strip().upper()
    parts = _STEM_SEPARATORS.split(text)
    if len(parts) < 2 or len(parts[-1]) > _MAX_STEM_SUFFIX:  # noqa: PLR2004
        return None
    stem = text[: len(text) - len(parts[-1]) - 1]
    return stem if len(stem) >= min_length else None


def _stem_keys(txn: NormalisedTransaction, min_length: int) -> set[str]:
    """A transaction's own stem and its whole reference: a split's single side
    carries the stem as its whole reference, and its parts carry it as their stem."""
    keys: set[str] = set()
    original = (txn.original_reference_text or "").strip().upper()
    if len(original) >= min_length:
        keys.add(original)
    stem = reference_stem(txn.original_reference_text, min_length)
    if stem:
        keys.add(stem)
    return keys


class GroupMatchingStrategy:
    def __init__(
        self,
        session: Session,
        run_id: str,
        config: GroupMatchConfig,
        *,
        fx: FxService | None = None,
        audit: AuditLogger | None = None,
        now: datetime | None = None,
    ) -> None:
        if audit is not None and now is None:
            raise ValueError("now is required when an audit logger is given")
        self._session = session
        self._run_id = run_id
        self._config = config
        self._fx = fx
        self._audit = audit
        self._now = now
        self._claims = ClaimsService(session)
        self._rejected: set[tuple[uuid.UUID, uuid.UUID]] = set()

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> GroupMatchOutcome:
        outcome = GroupMatchOutcome()
        consumed: set[uuid.UUID] = set()
        self._rejected = MatchingRepository(self._session).rejected_pairs(
            t.id for t in internal_candidates
        )
        self._pass("SPLIT", internal_candidates, external_candidates, consumed, outcome)
        self._pass("NETTED", external_candidates, internal_candidates, consumed, outcome)
        return outcome

    # -- one pass --------------------------------------------------------

    def _pass(
        self,
        kind: str,
        singles: list[NormalisedTransaction],
        pool: list[NormalisedTransaction],
        consumed: set[uuid.UUID],
        outcome: GroupMatchOutcome,
    ) -> None:
        stem_index, party_index = self._index(pool)
        max_members = (
            self._config.max_split_members if kind == "SPLIT" else self._config.max_netted_members
        )
        for single in singles:
            if single.id in consumed:
                continue
            solution = self._solve(
                single,
                stem_index=stem_index,
                party_index=party_index,
                consumed=consumed,
                max_members=max_members,
                outcome=outcome,
            )
            if solution is None:
                continue
            if self._record(kind, single, solution, outcome):
                consumed.add(single.id)
                consumed.update(m.id for m in solution.members)

    def _index(
        self, pool: list[NormalisedTransaction]
    ) -> tuple[dict[str, list[NormalisedTransaction]], dict[str, list[NormalisedTransaction]]]:
        stems: dict[str, list[NormalisedTransaction]] = defaultdict(list)
        parties: dict[str, list[NormalisedTransaction]] = defaultdict(list)
        for txn in pool:
            for key in _stem_keys(txn, self._config.min_stem_length):
                stems[key].append(txn)
            party = counterparty_key(
                txn.counterparty_name_normalised, self._config.counterparty_prefix_length
            )
            if party:
                parties[party].append(txn)
        return stems, parties

    def _is_rejected(self, single: NormalisedTransaction, member: NormalisedTransaction) -> bool:
        """True if a person unmatched this internal/external pairing earlier."""
        internal, external = (single, member) if single.source == "INTERNAL" else (member, single)
        return (internal.id, external.id) in self._rejected

    def _candidates(
        self,
        single: NormalisedTransaction,
        index: dict[str, list[NormalisedTransaction]],
        keys: set[str],
        consumed: set[uuid.UUID],
    ) -> list[NormalisedTransaction]:
        seen: dict[uuid.UUID, NormalisedTransaction] = {}
        for key in keys:
            for txn in index.get(key, []):
                if (
                    txn.id not in consumed
                    and not self._is_rejected(single, txn)
                    and txn.direction == single.direction
                    and abs((txn.txn_date - single.txn_date).days) <= self._config.date_window_days
                ):
                    seen[txn.id] = txn
        return sorted(seen.values(), key=lambda t: (t.txn_date, str(t.id)))

    # -- solving ---------------------------------------------------------

    def _solve(
        self,
        single: NormalisedTransaction,
        *,
        stem_index: dict[str, list[NormalisedTransaction]],
        party_index: dict[str, list[NormalisedTransaction]],
        consumed: set[uuid.UUID],
        max_members: int,
        outcome: GroupMatchOutcome,
    ) -> _Solution | None:
        stem_candidates = self._candidates(
            single, stem_index, _stem_keys(single, self._config.min_stem_length), consumed
        )
        party = counterparty_key(
            single.counterparty_name_normalised, self._config.counterparty_prefix_length
        )
        party_candidates = self._candidates(single, party_index, {party}, consumed) if party else []
        for link, confidence, candidates in (
            ("reference stem", _CONFIDENCE_STEM, stem_candidates),
            ("counterparty", _CONFIDENCE_COUNTERPARTY, party_candidates),
        ):
            if len(candidates) < 2:  # noqa: PLR2004
                continue
            solution = self._fit(
                single,
                candidates,
                link=link,
                confidence=confidence,
                max_members=max_members,
                outcome=outcome,
            )
            if solution is not None:
                return solution
        return None

    def _fit(
        self,
        single: NormalisedTransaction,
        candidates: list[NormalisedTransaction],
        *,
        link: str,
        confidence: Decimal,
        max_members: int,
        outcome: GroupMatchOutcome,
    ) -> _Solution | None:
        legs = self._legs(single, candidates)
        if legs is None:
            outcome.missing_fx_count += 1
            return None
        target, amounts, low, high, fx_quotes = legs

        chosen: list[int] | None = None
        if 2 <= len(candidates) <= max_members and low <= sum(amounts) <= high:  # noqa: PLR2004
            chosen = list(range(len(candidates)))  # the whole linked set settles in full
        elif len(candidates) > self._config.max_search_candidates:
            outcome.too_complex_count += 1
            return None
        else:
            found = find_unique_subset(
                amounts,
                low,
                high,
                max_size=max_members,
                max_states=self._config.max_dp_states,
            )
            if found.outcome is SubsetOutcome.AMBIGUOUS:
                outcome.ambiguous_count += 1
                return None
            if found.outcome is SubsetOutcome.TOO_COMPLEX:
                outcome.too_complex_count += 1
                return None
            if found.outcome is SubsetOutcome.UNIQUE:
                chosen = sorted(found.indices)
        if chosen is None:
            return None

        members = [candidates[i] for i in chosen]
        members_minor = sum(amounts[i] for i in chosen)
        fx = self._fx_details(single, members, target, members_minor, fx_quotes)
        return _Solution(
            members=members,
            confidence=_CONFIDENCE_FX if fx else confidence,
            link=link,
            fx=fx,
            single_minor=target,
            members_minor=members_minor,
        )

    def _legs(
        self, single: NormalisedTransaction, candidates: list[NormalisedTransaction]
    ) -> tuple[int, list[int], int, int, dict[str, FxQuote]] | None:
        """(target, member amounts, low, high, quotes used) in one comparison
        currency, or None when a needed rate is unavailable."""
        currencies = {single.currency, *(c.currency for c in candidates)}
        if len(currencies) == 1:
            target = single.amount_minor
            tol = self._config.tolerance_minor
            return target, [c.amount_minor for c in candidates], target - tol, target + tol, {}
        if self._fx is None:
            return None
        quotes: dict[str, FxQuote] = {}
        converted: list[int] = []
        for leg in (single, *candidates):
            quote = self._fx.quote(leg.currency, leg.txn_date)
            if quote is None:
                return None
            quotes[f"{leg.currency}@{leg.txn_date.isoformat()}"] = quote
            converted.append(to_inr_minor(leg.amount_minor, leg.currency, quote.rate_inr))
        target, amounts = converted[0], converted[1:]
        t = self._config.fx_tolerance_percent / _HUNDRED
        low = int((Decimal(target) * (_TWO - t) / (_TWO + t)).to_integral_value(ROUND_CEILING))
        high = int((Decimal(target) * (_TWO + t) / (_TWO - t)).to_integral_value(ROUND_FLOOR))
        return target, amounts, low, high, quotes

    @staticmethod
    def _fx_details(
        single: NormalisedTransaction,
        members: list[NormalisedTransaction],
        single_inr: int,
        members_inr: int,
        quotes: dict[str, FxQuote],
    ) -> dict[str, Any] | None:
        if not quotes:
            return None
        foreign_legs = [t for t in (single, *members) if t.currency != "INR"]
        foreign = foreign_legs[0]
        quote = quotes[f"{foreign.currency}@{foreign.txn_date.isoformat()}"]
        foreign_total = sum(t.amount_minor for t in foreign_legs)
        # An implied rate is only meaningful for a plain two-currency group: the
        # foreign side all in one currency, the other side all INR.
        single_is_foreign = single.currency != "INR"
        one_foreign_currency = len({t.currency for t in foreign_legs}) == 1
        other_side_inr = (
            all(m.currency == "INR" for m in members)
            if single_is_foreign
            else len(foreign_legs) == len(members)
        )
        rate = (
            implied_rate(
                members_inr if single_is_foreign else single_inr, foreign_total, foreign.currency
            )
            if one_foreign_currency and other_side_inr
            else None
        )
        return {
            "foreign_currency": foreign.currency,
            "foreign_amount_minor": foreign_total,
            "single_inr_minor": single_inr,
            "members_inr_minor": members_inr,
            "reference_rate_inr": str(quote.rate_inr),
            "reference_rate_source": quote.source,
            "reference_rate_date": quote.rate_date.isoformat(),
            "reference_rate_days_stale": quote.days_stale,
            "implied_rate_inr": str(rate.quantize(Decimal("0.0001"))) if rate else None,
            "variance_percent": str(
                relative_difference_percent(single_inr, members_inr).quantize(Decimal("0.0001"))
            ),
        }

    # -- recording -------------------------------------------------------

    def _record(
        self,
        kind: str,
        single: NormalisedTransaction,
        solution: _Solution,
        outcome: GroupMatchOutcome,
    ) -> bool:
        single_role = "INTERNAL" if kind == "SPLIT" else "EXTERNAL"
        member_role = "EXTERNAL" if kind == "SPLIT" else "INTERNAL"
        try:
            claims = self._claims.claim_group(
                [(single.id, single_role), *((m.id, member_role) for m in solution.members)]
            )
        except ClaimConflictError:
            outcome.skipped_claim_conflict_count += 1
            return False

        group_id = uuid.uuid4()
        rule_id = (
            "NETTED_CROSS_CURRENCY"
            if kind == "NETTED" and solution.fx
            else "SPLIT_TRANSACTION" if kind == "SPLIT" else "NETTED_SETTLEMENT"
        )
        rationale = self._rationale(kind, solution)
        member_ids = [str(m.id) for m in solution.members]
        first_result_id: uuid.UUID | None = None
        for member, claim in zip(solution.members, claims[1:], strict=True):
            internal, external = (single, member) if kind == "SPLIT" else (member, single)
            result = MatchResult(
                run_id=self._run_id,
                match_type=kind,
                status="AUTO_MATCHED",
                confidence=solution.confidence,
                internal_transaction_id=internal.id,
                external_transaction_id=external.id,
                field_scores={"group_sum": 1.0},
                matched_fields={
                    "match_group_id": str(group_id),
                    "group_size": len(solution.members) + 1,
                    "single_side": single_role,
                    "single_transaction_id": str(single.id),
                    "member_transaction_ids": member_ids,
                    "single_amount_minor": solution.single_minor,
                    "members_amount_minor": solution.members_minor,
                    "link": solution.link,
                    **({"fx": solution.fx} if solution.fx else {}),
                },
                hard_constraints_passed=True,
                rule_id=rule_id,
                match_group_id=group_id,
                candidate_count=len(solution.members),
                rationale=rationale,
                matched_on_date=external.txn_date,
                weights_version=None,
            )
            self._session.add(result)
            self._session.flush()
            self._claims.finalise(claim, result.id)
            if first_result_id is None:
                first_result_id = result.id
                self._claims.finalise(claims[0], result.id)
        for txn in (single, *solution.members):
            txn.match_status = "MATCHED"

        outcome.match_group_ids.append(group_id)
        if kind == "SPLIT":
            outcome.split_groups += 1
        else:
            outcome.netted_groups += 1
        self._audit_group(
            kind,
            group_id=group_id,
            single=single,
            solution=solution,
            rule_id=rule_id,
            rationale=rationale,
        )
        return True

    @staticmethod
    def _rationale(kind: str, solution: _Solution) -> str:
        what = (
            "one internal transaction settled as"
            if kind == "SPLIT"
            else "internal transactions netted into"
        )
        base = (
            f"{kind}: {len(solution.members)} entries ({what} "
            f"{'several external entries' if kind == 'SPLIT' else 'one external entry'}), "
            f"linked by {solution.link}; amounts {solution.members_minor} vs "
            f"{solution.single_minor} minor units"
        )
        if solution.fx:
            fx = solution.fx
            return (
                f"{base}. Cross-currency: {fx['foreign_currency']} "
                f"{fx['foreign_amount_minor']} minor units against INR {fx['members_inr_minor']} "
                f"/ {fx['single_inr_minor']} paise at reference rate {fx['reference_rate_inr']} "
                f"({fx['reference_rate_source']}, {fx['reference_rate_date']}); implied rate "
                f"{fx['implied_rate_inr']}; variance {fx['variance_percent']}%."
            )
        return f"{base}."

    def _audit_group(
        self,
        kind: str,
        *,
        group_id: uuid.UUID,
        single: NormalisedTransaction,
        solution: _Solution,
        rule_id: str,
        rationale: str,
    ) -> None:
        if self._audit is None or self._now is None:
            return
        self._audit.append(
            chain_id=MATCHING_CHAIN,
            actor_type="SYSTEM",
            actor_id="group-matcher",
            action_type=AuditActionType.MATCH,
            occurred_at=self._now,
            affected_records={
                "match_group_id": str(group_id),
                "single_transaction_id": str(single.id),
                "member_transaction_ids": [str(m.id) for m in solution.members],
            },
            after_state={
                "match_type": kind,
                "status": "AUTO_MATCHED",
                "confidence": str(solution.confidence),
                "rule_id": rule_id,
                "single_amount_minor": solution.single_minor,
                "members_amount_minor": solution.members_minor,
                **({"fx": solution.fx} if solution.fx else {}),
            },
            rationale=rationale,
        )
