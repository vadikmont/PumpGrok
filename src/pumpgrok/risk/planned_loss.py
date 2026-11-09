"""Fail-closed planned-loss gate.

This is not the position-notional cap (``max_trade_fraction``). A trade is
eligible only when every policy input is passed in explicitly. A missing
basis, stop, cost policy, or amount refuses. No code default is treated as
an owner choice. The opening illustration of 500 USD is not, by itself, a
basis for later equity.
"""

from __future__ import annotations

from dataclasses import dataclass

_BASIS = frozenset({"opening_balance", "current_equity"})


@dataclass(frozen=True)
class PlannedLossAssessment:
    """One assessment. ``eligible`` is false until every explicit input is present."""

    eligible: bool
    reason: str
    ceiling_cents: int | None = None
    planned_loss_cents: int | None = None
    basis: str | None = None


def _refuse(reason: str, basis: str | None = None) -> PlannedLossAssessment:
    return PlannedLossAssessment(False, reason, basis=basis)


def _whole(value: int | None, reason: str) -> str | None:
    if value is None:
        return reason
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "invalid_amount"
    return None


def _first(reasons: list[str | None]) -> str | None:
    for reason in reasons:
        if reason is not None:
            return reason
    return None


def _fraction_gap(numerator: int | None, denominator: int | None) -> str | None:
    gap = _whole(numerator, "fraction_unconfirmed")
    if gap is not None:
        return gap
    if (
        denominator is None
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
    ):
        return "fraction_unconfirmed"
    return None


def _cost_gap(inside: bool | None, fee_cents: int | None, slippage_bps: int | None) -> str | None:
    if not isinstance(inside, bool):
        return "cost_policy_unconfirmed"
    if not inside:
        return None
    return _first(
        [
            _whole(fee_cents, "fee_unconfirmed"),
            _whole(slippage_bps, "slippage_unconfirmed"),
        ]
    )


def _base_gap(
    basis: str, opening_balance_cents: int | None, current_equity_cents: int | None
) -> str | None:
    if basis == "opening_balance":
        return _whole(opening_balance_cents, "opening_unconfirmed")
    return _whole(current_equity_cents, "current_equity_unconfirmed")


def _policy_gap(
    *,
    basis: str | None,
    loss_fraction_numerator: int | None,
    loss_fraction_denominator: int | None,
    stop_distance_bps: int | None,
    notional_cents: int | None,
    costs_inside_budget: bool | None,
    fee_cents: int | None,
    slippage_bps: int | None,
    opening_balance_cents: int | None,
    current_equity_cents: int | None,
) -> str | None:
    if basis not in _BASIS:
        return "basis_unconfirmed"
    assert isinstance(basis, str)
    return _first(
        [
            _fraction_gap(loss_fraction_numerator, loss_fraction_denominator),
            _whole(stop_distance_bps, "stop_unconfirmed"),
            _cost_gap(costs_inside_budget, fee_cents, slippage_bps),
            _whole(notional_cents, "notional_unconfirmed"),
            _base_gap(basis, opening_balance_cents, current_equity_cents),
        ]
    )


def _cents(notional: int, bps: int) -> int:
    return notional * bps // 10_000


def assess_planned_loss(
    *,
    opening_balance_cents: int | None = None,
    current_equity_cents: int | None = None,
    basis: str | None = None,
    loss_fraction_numerator: int | None = None,
    loss_fraction_denominator: int | None = None,
    stop_distance_bps: int | None = None,
    notional_cents: int | None = None,
    costs_inside_budget: bool | None = None,
    fee_cents: int | None = None,
    slippage_bps: int | None = None,
) -> PlannedLossAssessment:
    """Refuse unless basis, stop, costs, and amounts were all passed explicitly.

    ``None`` means the owner has not chosen that input. Defaults on this
    signature are the missing state, not a risk policy. Arithmetic below runs
    only after those inputs are present. It does not read ``RiskBudget``.
    """
    gap = _policy_gap(
        basis=basis,
        loss_fraction_numerator=loss_fraction_numerator,
        loss_fraction_denominator=loss_fraction_denominator,
        stop_distance_bps=stop_distance_bps,
        notional_cents=notional_cents,
        costs_inside_budget=costs_inside_budget,
        fee_cents=fee_cents,
        slippage_bps=slippage_bps,
        opening_balance_cents=opening_balance_cents,
        current_equity_cents=current_equity_cents,
    )
    if gap is not None:
        return _refuse(gap, basis if basis in _BASIS else None)
    assert basis is not None
    assert loss_fraction_numerator is not None
    assert loss_fraction_denominator is not None
    assert stop_distance_bps is not None
    assert notional_cents is not None
    assert costs_inside_budget is not None
    base = opening_balance_cents if basis == "opening_balance" else current_equity_cents
    assert base is not None
    ceiling = base * loss_fraction_numerator // loss_fraction_denominator
    planned = _cents(notional_cents, stop_distance_bps)
    if costs_inside_budget:
        assert fee_cents is not None and slippage_bps is not None
        planned += fee_cents + _cents(notional_cents, slippage_bps)
    allowed = planned <= ceiling
    reason = "within_explicit_inputs" if allowed else "planned_loss_above_ceiling"
    return PlannedLossAssessment(allowed, reason, ceiling, planned, basis)
