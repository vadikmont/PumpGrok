"""Explicit USD paper-account records.

Amounts are integer US cents. They are not lamports and not SOL. Nothing in
this module selects a planned-loss basis, and nothing writes a balance unless
the caller passes an amount and the currency ``USD``.
"""

from __future__ import annotations

from typing import Any

from pumpgrok.ledger.store import LedgerStore

OPENING_CENTS_KEY = "usd_opening_balance_cents"
CURRENCY_KEY = "usd_account_currency"
BASIS_KEY = "usd_planned_loss_basis"
_CONFIRMED_BASIS = frozenset({"opening_balance", "current_equity"})


class UsdAccountError(ValueError):
    """The USD account request is incomplete or not USD cents."""


def _require_cents(amount: int, label: str) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise UsdAccountError(f"{label} must be a non-negative int of US cents")


def _require_fraction(numerator: int, denominator: int) -> None:
    if isinstance(numerator, bool) or not isinstance(numerator, int) or numerator < 0:
        raise UsdAccountError("loss fraction numerator must be a non-negative int")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator <= 0:
        raise UsdAccountError("loss fraction denominator must be a positive int")


def record_opening_balance(store: LedgerStore, *, amount_cents: int, currency: str) -> None:
    """Record one explicit USD opening balance. Does not write lamports or a basis."""
    if currency != "USD":
        raise UsdAccountError("currency must be USD")
    _require_cents(amount_cents, "amount_cents")
    if store.get_meta(OPENING_CENTS_KEY) is not None or store.get_meta(CURRENCY_KEY) is not None:
        raise UsdAccountError("USD opening balance is already recorded")
    store.set_meta(CURRENCY_KEY, "USD")
    store.set_meta(OPENING_CENTS_KEY, str(amount_cents))


def read_usd_account(store: LedgerStore) -> dict[str, Any]:
    """Read the USD account. Missing data stays unconfigured, not zero."""
    raw_cents = store.get_meta(OPENING_CENTS_KEY)
    currency = store.get_meta(CURRENCY_KEY)
    basis = store.get_meta(BASIS_KEY)
    confirmed = basis if basis in _CONFIRMED_BASIS else "UNCONFIRMED"
    if raw_cents is None and currency is None:
        return {
            "status": "UNCONFIGURED",
            "currency": None,
            "opening_balance_cents": None,
            "planned_loss_basis": confirmed,
            "initial_equity_lamports": store.get_meta("initial_equity_lamports"),
        }
    if currency != "USD" or raw_cents is None or not str(raw_cents).isdigit():
        return {
            "status": "UNAVAILABLE",
            "currency": currency,
            "opening_balance_cents": None,
            "planned_loss_basis": confirmed,
            "initial_equity_lamports": store.get_meta("initial_equity_lamports"),
        }
    return {
        "status": "RECORDED",
        "currency": "USD",
        "opening_balance_cents": int(raw_cents),
        "planned_loss_basis": confirmed,
        "initial_equity_lamports": store.get_meta("initial_equity_lamports"),
    }


def illustrative_opening_planned_loss_cents(
    opening_cents: int, numerator: int, denominator: int
) -> int:
    """Integer illustration of a loss ceiling on an opening balance.

    ``10_000_000`` cents times ``5/1000`` is ``50_000`` cents (500 USD). That
    figure is the opening arithmetic only. It does not choose whether later
    equity changes the basis, and it does not authorize a trade.
    """
    _require_cents(opening_cents, "opening_cents")
    _require_fraction(numerator, denominator)
    return opening_cents * numerator // denominator
