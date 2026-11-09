"""Planned-loss gate. Fixture numbers are not desk capital or a chosen basis policy."""

from __future__ import annotations

from pumpgrok.risk.planned_loss import assess_planned_loss

# Opening illustration only: 100000 USD in cents, 0.5 percent, 500 USD in cents.
_OPENING = 10_000_000
_NUM = 5
_DEN = 1000
_CEILING = 50_000


def test_missing_basis_refuses_even_when_notional_is_half_a_percent() -> None:
    """A notional of 500 USD is not a planned-loss approval."""
    result = assess_planned_loss(
        opening_balance_cents=_OPENING,
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        notional_cents=_CEILING,
    )
    assert result.eligible is False
    assert result.reason == "basis_unconfirmed"
    assert result.ceiling_cents is None
    assert result.planned_loss_cents is None


def test_opening_basis_without_stop_or_cost_policy_refuses() -> None:
    result = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        notional_cents=1_000_000,
    )
    assert result.eligible is False
    assert result.reason == "stop_unconfirmed"

    with_stop = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=200,
        notional_cents=1_000_000,
    )
    assert with_stop.reason == "cost_policy_unconfirmed"
    assert with_stop.eligible is False


def test_current_equity_basis_does_not_reuse_the_opening_balance() -> None:
    result = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="current_equity",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=100,
        notional_cents=100,
        costs_inside_budget=False,
    )
    assert result.eligible is False
    assert result.reason == "current_equity_unconfirmed"


def test_explicit_fixture_inputs_compare_loss_to_the_opening_ceiling() -> None:
    """Fixture-only: 200 bps stop on 20000 USD notional is 400 USD, under 500."""
    under = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=200,
        notional_cents=2_000_000,
        costs_inside_budget=False,
    )
    assert under.ceiling_cents == _CEILING
    assert under.planned_loss_cents == 40_000
    assert under.eligible is True
    assert under.reason == "within_explicit_inputs"

    over = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=1_000,
        notional_cents=1_000_000,
        costs_inside_budget=False,
    )
    assert over.planned_loss_cents == 100_000
    assert over.eligible is False
    assert over.reason == "planned_loss_above_ceiling"


def test_costs_inside_budget_add_fee_and_slippage_when_explicitly_included() -> None:
    result = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=100,
        notional_cents=2_000_000,
        costs_inside_budget=True,
        fee_cents=20_000,
        slippage_bps=100,
    )
    # 200 USD stop plus 200 USD fee plus 200 USD slippage.
    assert result.planned_loss_cents == 60_000
    assert result.eligible is False
    assert result.reason == "planned_loss_above_ceiling"

    missing_fee = assess_planned_loss(
        opening_balance_cents=_OPENING,
        basis="opening_balance",
        loss_fraction_numerator=_NUM,
        loss_fraction_denominator=_DEN,
        stop_distance_bps=100,
        notional_cents=100,
        costs_inside_budget=True,
        slippage_bps=1,
    )
    assert missing_fee.reason == "fee_unconfirmed"
    assert missing_fee.eligible is False
