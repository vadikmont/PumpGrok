"""Read-only paper terminal: missing, unconfigured, recorded zero, and real rows."""

from __future__ import annotations

import json
import sqlite3
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from pumpgrok.ledger.store import LedgerStore, _row_hash
from pumpgrok.reporting.terminal import (
    bind_terminal,
    build_snapshot,
    render_terminal_html,
    serve_bound,
)

_MINT = "So11111111111111111111111111111111111111112"


def _note(
    store: LedgerStore,
    key: str,
    text: str,
    *,
    kind: str = "note",
    refs: list[str] | None = None,
) -> None:
    store.append(
        ts_utc="2026-01-02T00:00:00.000000Z",
        source="paper",
        correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
        idempotency_key=key,
        type="JournalNote",
        payload={
            "source": "paper",
            "seat": "desk",
            "note_kind": kind,
            "text": text,
            "refs": refs or [],
        },
    )


def test_missing_ledger_is_not_created_and_not_zero(tmp_path: Path) -> None:
    path = tmp_path / "var" / "ledger.sqlite"
    snapshot = build_snapshot(path, requested_mode="paper")
    assert not path.exists()
    assert snapshot["ledger"]["presence"] == "UNAVAILABLE"
    assert snapshot["ledger"]["event_count"] is None
    assert snapshot["capital"]["initial_equity_lamports"] is None
    assert snapshot["capital"]["interpretation"] == "UNAVAILABLE"
    assert snapshot["equity"]["status"] == "UNAVAILABLE"
    assert snapshot["equity"]["lamports"] is None
    assert snapshot["positions"]["status"] == "UNAVAILABLE"
    assert snapshot["positions"]["open_count"] is None
    assert snapshot["pnl"]["status"] == "UNAVAILABLE"
    assert snapshot["execution"]["places_orders"] is False
    assert snapshot["execution"]["creates_ledger"] is False
    rendered = json.dumps(snapshot)
    assert "10000000000" not in rendered
    page = render_terminal_html(snapshot)
    assert "UNAVAILABLE" in page
    assert "FOLDED 0" not in page
    assert "signs" not in page.lower() or "Signs: false" in page


def test_empty_ledger_capital_is_unconfigured_positions_are_none(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    store = LedgerStore(path)
    store.close()
    snapshot = build_snapshot(path)
    assert snapshot["ledger"]["presence"] == "PRESENT"
    assert snapshot["ledger"]["chain"] == "CONSISTENT"
    assert snapshot["ledger"]["event_count"] == 0
    assert snapshot["capital"]["meta_status"] == "ABSENT"
    assert snapshot["capital"]["interpretation"] == "UNCONFIGURED"
    assert snapshot["capital"]["initial_equity_lamports"] is None
    assert snapshot["capital"]["human_capital_record"] == "ABSENT"
    assert snapshot["capital"]["meta_in_hash_chain"] is False
    assert snapshot["equity"]["status"] == "UNCONFIGURED"
    assert snapshot["equity"]["lamports"] is None
    assert snapshot["cash"]["status"] == "UNCONFIGURED"
    assert snapshot["positions"]["status"] == "NONE"
    assert snapshot["positions"]["open_count"] == 0
    assert snapshot["tickets"]["status"] == "NONE"
    assert snapshot["position_closed_rows"] == {"status": "KNOWN", "count": 0}
    assert snapshot["risk_limits"]["status"] == "UNCONFIGURED"
    assert snapshot["risk_limits"]["code_defaults_applied_to_this_view"] is False
    assert snapshot["autonomy"]["status"] == "UNCONFIGURED"
    page = render_terminal_html(snapshot)
    assert "UNCONFIGURED" in page
    assert "NONE 0" in page
    assert "FOLDED 0" not in page
    assert "10000000000" not in page


def test_recorded_zero_equity_stays_zero(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    store = LedgerStore(path)
    try:
        store.set_meta("initial_equity_lamports", "0")
    finally:
        store.close()
    snapshot = build_snapshot(path)
    assert snapshot["capital"]["interpretation"] == "RECORDED_META_ONLY"
    assert snapshot["capital"]["initial_equity_lamports"] == 0
    assert snapshot["equity"] == {"status": "FOLDED", "lamports": 0}
    assert snapshot["cash"] == {"status": "FOLDED", "lamports": 0}
    page = render_terminal_html(snapshot)
    assert "Equity: FOLDED 0" in page
    assert "Initial equity lamports: RECORDED_META_ONLY 0" in page


def test_real_rows_fold_and_escape(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    store = LedgerStore(path)
    try:
        store.set_meta("initial_equity_lamports", "1000")
        _note(store, "<script>", "lead", kind="ticket", refs=["SOL-20261108-001"])
        store.append(
            ts_utc="2026-01-02T00:00:01.000000Z",
            source="paper",
            correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
            idempotency_key="open-1",
            type="PositionOpened",
            payload={
                "source": "paper",
                "mint": _MINT,
                "price_entry": "0.00001",
                "token_units": 3,
                "cost_basis_lamports": 25,
                "opened_ts_utc": "2026-01-02T00:00:01.000000Z",
                "symbol": "TST",
                "creator": "Creator1111111111111111111111111111111111",
                "ticket_id": "SOL-20261108-001",
                "score": "0.8",
            },
        )
    finally:
        store.close()
    snapshot = build_snapshot(path)
    assert snapshot["ledger"]["chain"] == "CONSISTENT"
    assert snapshot["tickets"] == {"status": "PRESENT", "ids": ["SOL-20261108-001"]}
    assert snapshot["positions"]["status"] == "OPEN"
    assert snapshot["positions"]["open_count"] == 1
    assert snapshot["positions"]["mints"] == [_MINT]
    assert snapshot["equity"]["status"] == "FOLDED"
    assert snapshot["equity"]["lamports"] == 1025
    assert snapshot["pnl"]["status"] == "UNAVAILABLE"
    assert snapshot["recent_events"]["status"] == "PRESENT"
    page = render_terminal_html(snapshot)
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "SOL-20261108-001" in page


def test_inconsistent_chain_hides_equity(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts_utc TEXT, source TEXT,"
        " correlation_id TEXT, idempotency_key TEXT, type TEXT, payload_json TEXT,"
        " prev_hash TEXT, hash TEXT)"
    )
    db.execute("INSERT INTO meta (key, value) VALUES ('initial_equity_lamports', '5')")
    db.execute(
        "INSERT INTO events VALUES (1, '2026-01-01T00:00:00.000000Z', 'paper', 'c',"
        " 'k', 'JournalNote', '{}', ?, 'not-a-hash')",
        ("0" * 64,),
    )
    db.commit()
    db.close()
    snapshot = build_snapshot(path)
    assert snapshot["ledger"]["chain"] == "INCONSISTENT"
    assert snapshot["capital"]["meta_status"] == "PRESENT"
    assert snapshot["capital"]["initial_equity_lamports"] is None
    assert snapshot["capital"]["interpretation"] == "UNAVAILABLE"
    assert snapshot["equity"]["status"] == "UNAVAILABLE"
    assert snapshot["positions"]["status"] == "UNAVAILABLE"


def test_live_request_does_not_add_a_signer(tmp_path: Path) -> None:
    snapshot = build_snapshot(tmp_path / "missing.sqlite", requested_mode="live")
    assert snapshot["requested_mode"] == "live"
    assert snapshot["viewer"]["signs"] is False
    assert any("does not start live execution" in item for item in snapshot["blockers"])


def test_http_is_loopback_read_only(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.sqlite"
    server = bind_terminal(ledger, 0)
    host, port = server.server_address[0], int(server.server_address[1])
    assert host == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/terminal.json") as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["ledger"]["presence"] == "UNAVAILABLE"
        assert not ledger.exists()
        with urlopen(f"http://127.0.0.1:{port}/") as page:
            html = page.read().decode("utf-8")
        assert "UNAVAILABLE" in html
        assert "POST is refused" in html
        request = Request(f"http://127.0.0.1:{port}/", data=b"{}", method="POST")
        with pytest.raises(HTTPError) as excinfo:
            urlopen(request)
        assert excinfo.value.code == 405
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("DELETE", "/terminal.json")
        deleted = connection.getresponse()
        assert deleted.status == 405
        connection.close()
        missing = HTTPConnection("127.0.0.1", port, timeout=5)
        missing.request("GET", "/orders")
        not_found = missing.getresponse()
        assert not_found.status == 404
        missing.close()
    finally:
        server.shutdown()
        server.server_close()
    assert not ledger.exists()


def test_unreadable_paths_and_bad_payloads(tmp_path: Path) -> None:
    directory = tmp_path / "dir.sqlite"
    directory.mkdir()
    assert build_snapshot(directory)["ledger"]["presence"] == "UNREADABLE"

    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    assert build_snapshot(empty)["ledger"]["chain"] == "UNREADABLE"

    broken = tmp_path / "broken.sqlite"
    _write_raw_event(broken, payload_json="not-json", prev_hash="0" * 64, seq=1)
    snapshot = build_snapshot(broken)
    assert snapshot["ledger"]["chain"] == "CONSISTENT"
    assert snapshot["equity"]["status"] == "UNAVAILABLE"

    listed = tmp_path / "list.sqlite"
    _write_raw_event(listed, payload_json="[]", prev_hash="0" * 64, seq=1)
    assert build_snapshot(listed)["equity"]["status"] == "UNAVAILABLE"

    chained = tmp_path / "chain.sqlite"
    _write_raw_event(chained, payload_json="{}", prev_hash="1" * 64, seq=1)
    assert build_snapshot(chained)["ledger"]["chain"] == "INCONSISTENT"


def test_recorded_controls_and_garbage_equity(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    store = LedgerStore(path)
    try:
        store.set_meta("initial_equity_lamports", "nope")
        store.append(
            ts_utc="2026-01-02T00:00:00.000000Z",
            source="paper",
            correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
            idempotency_key="bad-autonomy",
            type="ConfigChanged",
            payload={
                "source": "paper",
                "by": "human:op",
                "keys_changed": ["autonomy_level"],
                "old_hash": "",
                "new_hash": "nope",
            },
        )
        store.append(
            ts_utc="2026-01-02T00:00:01.000000Z",
            source="paper",
            correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
            idempotency_key="autonomy",
            type="ConfigChanged",
            payload={
                "source": "paper",
                "by": "human:op",
                "keys_changed": ["autonomy_level", "paper_equity_sol"],
                "old_hash": "manual-ticket",
                "new_hash": "manual-ticket",
            },
        )
        _note(store, "plain", "hello")
        _note(store, "bare-ticket", "bare", kind="ticket", refs=[])
        _note(store, "real-ticket", "real", kind="ticket", refs=["SOL-20261108-002"])
        store.append(
            ts_utc="2026-01-02T00:00:02.000000Z",
            source="paper",
            correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
            idempotency_key="halt",
            type="HaltSet",
            payload={
                "source": "paper",
                "reason": "manual",
                "detail": "test",
                "equity_lamports": 1,
                "day_start_equity_lamports": 1,
                "set_by": "risk_engine",
            },
        )
        store.append(
            ts_utc="2026-01-02T00:00:03.000000Z",
            source="paper",
            correlation_id="01JAAAAAAAAAAAAAAAAAAAAAAAAA",
            idempotency_key="kill",
            type="KillSwitchSet",
            payload={
                "source": "paper",
                "origin": "tool",
                "reason": "test",
                "flatten_requested": False,
                "set_by": "risk",
            },
        )
    finally:
        store.close()
    snapshot = build_snapshot(path)
    assert snapshot["capital"]["interpretation"] == "UNAVAILABLE"
    assert snapshot["capital"]["human_capital_record"] == "PRESENT"
    assert snapshot["autonomy"] == {"status": "RECORDED", "policy": "manual-ticket"}
    assert snapshot["risk_limits"]["status"] == "RECORDED"
    assert "paper_equity_sol" in snapshot["risk_limits"]["human_keys"]
    assert snapshot["tickets"]["ids"] == ["SOL-20261108-002"]
    assert snapshot["halt"]["status"] == "ACTIVE"
    assert snapshot["kill_switch"]["status"] == "ACTIVE"
    page = render_terminal_html(snapshot)
    assert "manual-ticket" in page

    negative = tmp_path / "negative.sqlite"
    store = LedgerStore(negative)
    try:
        store.set_meta("initial_equity_lamports", "-4")
    finally:
        store.close()
    folded = build_snapshot(negative)
    assert folded["capital"]["initial_equity_lamports"] == -4
    assert folded["equity"]["lamports"] == -4


def test_html_fallbacks_and_put(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    snapshot = build_snapshot(path)
    snapshot["blockers"] = "nope"
    snapshot["autonomy"] = {"status": "RECORDED", "policy": None}
    snapshot["tickets"] = {"status": "PRESENT", "ids": "nope"}
    snapshot["recent_events"] = {"status": "PRESENT", "rows": ["skip", {"seq": 1}]}
    page = render_terminal_html(snapshot)
    assert "UNAVAILABLE" in page
    assert "RECORDED" in page
    assert "<table>" in page

    server = bind_terminal(path, 0)
    handler = server.RequestHandlerClass
    handler.log_message(object(), "hit %s", "ok")
    handler.log_message(object(), "hit")
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{port}/index.html") as response:
            assert response.status == 200
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("PUT", "/")
        put = connection.getresponse()
        assert put.status == 405
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_serve_bound_returns_when_the_server_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _return(self: ThreadingHTTPServer) -> None:
        return None

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", _return)
    server = bind_terminal(tmp_path / "missing.sqlite", 0)
    assert serve_bound(server) == 0


def _write_raw_event(path: Path, *, payload_json: str, prev_hash: str, seq: int) -> None:
    digest = _row_hash(
        prev_hash=prev_hash,
        seq=seq,
        ts_utc="2026-01-01T00:00:00.000000Z",
        source="paper",
        correlation_id="c",
        idempotency_key=f"k-{seq}-{payload_json}",
        type="JournalNote",
        payload_json=payload_json,
    )
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts_utc TEXT, source TEXT,"
        " correlation_id TEXT, idempotency_key TEXT, type TEXT, payload_json TEXT,"
        " prev_hash TEXT, hash TEXT)"
    )
    db.execute(
        "INSERT INTO events VALUES (?, '2026-01-01T00:00:00.000000Z', 'paper', 'c',"
        " ?, 'JournalNote', ?, ?, ?)",
        (seq, f"k-{seq}-{payload_json}", payload_json, prev_hash, digest),
    )
    db.commit()
    db.close()


def test_non_list_config_and_connect_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "odd.sqlite"
    payload_json = '{"keys_changed":"nope"}'
    digest = _row_hash(
        prev_hash="0" * 64,
        seq=1,
        ts_utc="2026-01-01T00:00:00.000000Z",
        source="paper",
        correlation_id="c",
        idempotency_key="k-config",
        type="ConfigChanged",
        payload_json=payload_json,
    )
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts_utc TEXT, source TEXT,"
        " correlation_id TEXT, idempotency_key TEXT, type TEXT, payload_json TEXT,"
        " prev_hash TEXT, hash TEXT)"
    )
    db.execute(
        "INSERT INTO events VALUES (1, '2026-01-01T00:00:00.000000Z', 'paper', 'c',"
        " 'k-config', 'ConfigChanged', ?, ?, ?)",
        (payload_json, "0" * 64, digest),
    )
    db.execute("INSERT INTO meta VALUES ('initial_equity_lamports', '3')")
    db.commit()
    db.close()
    snapshot = build_snapshot(path)
    assert snapshot["ledger"]["chain"] == "CONSISTENT"
    assert snapshot["autonomy"]["status"] == "UNCONFIGURED"
    assert snapshot["risk_limits"]["status"] == "UNCONFIGURED"
    assert snapshot["capital"]["human_capital_record"] == "ABSENT"

    present = tmp_path / "present.sqlite"
    present.write_bytes(b"x")

    def _boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("nope")

    monkeypatch.setattr("pumpgrok.reporting.terminal.sqlite3.connect", _boom)
    failed = build_snapshot(present)
    assert failed["ledger"]["presence"] == "UNREADABLE"
    assert failed["ledger"]["error"] == "OperationalError"


def test_serve_bound_stops_on_interrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _interrupt(self: ThreadingHTTPServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", _interrupt)
    server = bind_terminal(tmp_path / "missing.sqlite", 0)
    assert serve_bound(server) == 0
    assert not (tmp_path / "missing.sqlite").exists()
