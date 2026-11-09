"""Starting capital is not invented by the paper composition root.

An empty ledger stays without ``initial_equity_lamports``. Shutdown and the
exit halt refuse a missing genesis instead of substituting the code-default
equity. A genesis some caller recorded explicitly is never overwritten.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pumpgrok.config.settings import load_settings
from pumpgrok.core.errors import CorruptState
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.replay.rng import make_rng
from pumpgrok.supervisor.lifecycle import shutdown
from pumpgrok.supervisor.scheduler import ROSTER, Supervisor
from pumpgrok.supervisor.seat_task import SeatContext, SeatTask
from pumpgrok.tools import build_operator_registry

ROOT = Path(__file__).resolve().parents[2]
START = 1_700_000_000.0


async def _ok(ctx: SeatContext) -> dict[str, Any]:
    return {"ok": True}


async def _compose(tmp_path: Path, name: str = "ledger.sqlite") -> Path:
    """Run the paper composition root over a fresh ledger; return its path."""
    ledger = tmp_path / name
    assert not ledger.exists()
    settings = load_settings()
    _reg, _manifests, client = build_operator_registry(
        data_dir=tmp_path / "desk",
        repo_root=ROOT,
        settings=settings,
        clock=FixtureClock(start=START),
        ledger_path=ledger,
        p_drop=0.0,
    )
    await client.aclose()
    return ledger


def test_paper_composition_leaves_genesis_unconfigured(tmp_path: Path) -> None:
    """A fresh paper ledger does not receive the code-default equity."""
    ledger = asyncio.run(_compose(tmp_path))
    store = LedgerStore(str(ledger))
    try:
        assert store.get_meta("initial_equity_lamports") is None
        assert str(load_settings().risk.paper_equity_lamports) != ""
    finally:
        store.close()


def test_paper_composition_never_overwrites_genesis(tmp_path: Path) -> None:
    """History keeps the genesis it started with when the budget later changes."""
    ledger = tmp_path / "ledger.sqlite"
    pre = LedgerStore(str(ledger))
    try:
        pre.set_meta("initial_equity_lamports", "12345")
    finally:
        pre.close()
    settings = load_settings()
    assert str(settings.risk.paper_equity_lamports) != "12345"
    _reg, _manifests, client = build_operator_registry(
        data_dir=tmp_path / "desk",
        repo_root=ROOT,
        settings=settings,
        clock=FixtureClock(start=START),
        ledger_path=ledger,
        p_drop=0.0,
    )
    asyncio.run(client.aclose())
    store = LedgerStore(str(ledger))
    try:
        assert store.get_meta("initial_equity_lamports") == "12345"
    finally:
        store.close()


async def test_fresh_paper_ledger_shutdown_refuses_missing_genesis(tmp_path: Path) -> None:
    """Shutdown does not invent equity when genesis was never recorded."""
    ledger = await _compose(tmp_path)
    clock = FixtureClock(start=START)
    store = LedgerStore(str(ledger))
    try:
        supervisor = Supervisor(
            store=store, clock=clock, rng=make_rng(11), cycles=dict.fromkeys(ROSTER, _ok)
        )
        with pytest.raises(CorruptState, match="initial_equity_lamports meta is missing"):
            await shutdown(
                supervisor,
                store=store,
                health_path=str(tmp_path / "health.json"),
                journal_dir=str(tmp_path / "journal"),
                trades_path=str(tmp_path / "trades.jsonl"),
            )
        assert store.get_meta("initial_equity_lamports") is None
    finally:
        store.close()


async def test_fresh_paper_ledger_exit_halt_writes_nothing_without_genesis(tmp_path: Path) -> None:
    """The exit halt is not stamped with a code-default equity."""
    ledger = await _compose(tmp_path)
    clock = FixtureClock(start=START)
    store = LedgerStore(str(ledger))
    try:
        task = SeatTask("bot-06-exit", 1.0, _ok, clock=clock, rng=make_rng(11), store=store)
        with pytest.raises(CorruptState, match="initial_equity_lamports meta is missing"):
            task._append_exit_halt("test-correlation")
        assert store.read(types=["HaltSet"], limit=None) == []
        assert store.get_meta("initial_equity_lamports") is None
    finally:
        store.close()
