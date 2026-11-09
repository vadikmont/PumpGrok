"""Replay open does not write the code-default paper equity.

The ledger path is a temporary file. It is not ``var/ledger.sqlite`` and it is
not desk capital or performance.
"""

from __future__ import annotations

from pathlib import Path

from pumpgrok.config.settings import FilterSettings
from pumpgrok.supervisor.scheduler import open_replay_runtime

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "tests" / "fixtures" / "replay" / "smoke"


def test_open_replay_runtime_does_not_stamp_default_equity(tmp_path: Path) -> None:
    ledger = tmp_path / "fixture-ledger.sqlite"
    runtime = open_replay_runtime(
        SMOKE,
        FilterSettings(),
        seed=1,
        ledger_path=ledger,
    )
    try:
        assert runtime.store.get_meta("initial_equity_lamports") is None
        assert runtime.store.count() == 0
    finally:
        runtime.store.close()
