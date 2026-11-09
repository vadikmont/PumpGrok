"""USD paper-account records. Temporary ledgers only, not desk capital."""

from __future__ import annotations

from pathlib import Path

import pytest

from pumpgrok.ledger.store import LedgerStore
from pumpgrok.ledger.usd_account import (
    UsdAccountError,
    illustrative_opening_planned_loss_cents,
    read_usd_account,
    record_opening_balance,
)

# Owner-stated opening figure, used here only as labelled arithmetic.
_OPENING_USD_CENTS = 10_000_000
_HALF_PERCENT = (5, 1000)


def test_unread_account_is_unconfigured(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "fixture.sqlite")
    try:
        view = read_usd_account(store)
        assert view["status"] == "UNCONFIGURED"
        assert view["opening_balance_cents"] is None
        assert view["planned_loss_basis"] == "UNCONFIRMED"
        assert view["initial_equity_lamports"] is None
        assert store.count() == 0
    finally:
        store.close()


def test_setter_requires_explicit_usd_cents(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "fixture.sqlite")
    try:
        with pytest.raises(UsdAccountError):
            record_opening_balance(store, amount_cents=_OPENING_USD_CENTS, currency="SOL")
        with pytest.raises(UsdAccountError):
            record_opening_balance(store, amount_cents=True, currency="USD")  # type: ignore[arg-type]
        with pytest.raises(UsdAccountError):
            record_opening_balance(store, amount_cents=-1, currency="USD")
        assert store.get_meta("usd_opening_balance_cents") is None
        assert store.get_meta("initial_equity_lamports") is None
    finally:
        store.close()


def test_explicit_opening_balance_is_cents_not_lamports(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "fixture.sqlite")
    try:
        record_opening_balance(store, amount_cents=_OPENING_USD_CENTS, currency="USD")
        view = read_usd_account(store)
        assert view["status"] == "RECORDED"
        assert view["currency"] == "USD"
        assert view["opening_balance_cents"] == _OPENING_USD_CENTS
        assert view["planned_loss_basis"] == "UNCONFIRMED"
        assert view["initial_equity_lamports"] is None
        with pytest.raises(UsdAccountError, match="already recorded"):
            record_opening_balance(store, amount_cents=1, currency="USD")
        assert store.get_meta("usd_opening_balance_cents") == str(_OPENING_USD_CENTS)
    finally:
        store.close()


def test_opening_illustration_is_500_usd_and_sets_no_policy(tmp_path: Path) -> None:
    """100000 USD times 0.5 percent is 500 USD. Basis after that stays unconfirmed."""
    cents = illustrative_opening_planned_loss_cents(_OPENING_USD_CENTS, *_HALF_PERCENT)
    assert cents == 50_000
    store = LedgerStore(tmp_path / "fixture.sqlite")
    try:
        record_opening_balance(store, amount_cents=_OPENING_USD_CENTS, currency="USD")
        assert read_usd_account(store)["planned_loss_basis"] == "UNCONFIRMED"
    finally:
        store.close()
