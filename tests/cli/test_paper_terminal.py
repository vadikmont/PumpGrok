"""CLI: the paper terminal and refused trades do not seed the canonical ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pumpgrok.cli import main
from pumpgrok.ledger.store import LedgerStore


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("PUMPGROK_") or any(
            frag in name.lower()
            for frag in ("secret", "password", "token", "api_key", "auth", "credential")
        ):
            monkeypatch.delenv(name, raising=False)


def _trade(seat: str) -> list[str]:
    return [
        "paper",
        "buy",
        "--ticket",
        "SOL-20260101-001",
        "--mint",
        "So11111111111111111111111111111111111111112",
        "--size-sol",
        "0.5",
        "--slippage-bps",
        "100",
        "--approval-id",
        "apr-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "--seat",
        seat,
    ]


def test_terminal_status_reads_nothing_into_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["terminal", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ledger"]["presence"] == "UNAVAILABLE"
    assert payload["equity"]["lamports"] is None
    assert payload["execution"]["places_orders"] is False
    assert not (tmp_path / "var" / "ledger.sqlite").exists()


def test_terminal_serve_refuses_public_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["terminal", "serve", "--host", "0.0.0.0", "--port", "9"]) == 1
    captured = capsys.readouterr()
    assert "127.0.0.1" in captured.err
    assert "listening" not in captured.out
    assert not (tmp_path / "var" / "ledger.sqlite").exists()


def test_terminal_serve_loopback_does_not_create_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def _stop(server: Any) -> int:
        server.server_close()
        return 0

    monkeypatch.setattr("pumpgrok.cli.verbs_terminal.serve_bound", _stop)
    assert main(["terminal", "serve", "--host", "127.0.0.1", "--port", "0"]) == 0
    assert "terminal listening http://127.0.0.1:" in capsys.readouterr().out
    assert not (tmp_path / "var" / "ledger.sqlite").exists()


def test_refused_buy_does_not_touch_an_existing_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "var" / "ledger.sqlite"
    store = LedgerStore(path)
    try:
        store.set_meta("initial_equity_lamports", "1")
    finally:
        store.close()
    before = path.stat()
    assert main(_trade("bot-04-sniper")) == 1
    after = path.stat()
    assert before.st_mtime_ns == after.st_mtime_ns
    assert before.st_size == after.st_size
    assert not Path(str(path) + "-wal").exists()
    again = LedgerStore(path)
    try:
        assert again.get_meta("initial_equity_lamports") == "1"
        assert again.count() == 0
    finally:
        again.close()


def test_terminal_serve_rejects_a_bad_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["terminal", "serve", "--port", "-1"]) == 1
    assert "0-65535" in capsys.readouterr().err
    assert not (tmp_path / "var" / "ledger.sqlite").exists()


def test_terminal_serve_bind_failure_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(_path: object, _port: int) -> object:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("pumpgrok.cli.verbs_terminal.bind_terminal", _boom)
    assert main(["terminal", "serve", "--port", "0"]) == 1
    assert "terminal bind failed" in capsys.readouterr().err


def test_tool_schema_help_does_not_create_canonical_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["tools", "onchain.authorities", "--help"]) == 0
    assert not (tmp_path / "var" / "ledger.sqlite").exists()
    assert not (tmp_path / "var" / "desk" / "binding.key").exists()
