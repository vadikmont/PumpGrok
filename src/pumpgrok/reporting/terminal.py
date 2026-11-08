"""Read-only paper terminal over the append-only ledger.

The page and the JSON snapshot open an existing ledger with sqlite ``mode=ro``.
A missing file stays missing. Absent capital is ``UNCONFIGURED`` with a null
lamport count. A recorded zero stays zero. Nothing here signs, broadcasts, or
places an order.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from pumpgrok.core.errors import PumpgrokError
from pumpgrok.ledger.reducer import initial_state, reduce
from pumpgrok.ledger.store import GENESIS_PREV_HASH, _row_hash
from pumpgrok.ledger.views import cash_lamports, equity_lamports, positions

log = logging.getLogger(__name__)

LOOPBACK_HOST = "127.0.0.1"
_RECENT_LIMIT = 20
_AUTONOMY_LEVELS = frozenset({"manual-ticket", "autonomous-paper"})

BLOCKERS: tuple[str, ...] = (
    "pumpgrok scout pipeline is registered and exits 5. It does not discover or trade.",
    "pumpgrok paper buy and paper sell refuse unavailable. No verified curve price "
    "source is bound, and the CLI writes no fill.",
    "The curve account layout is marked ASSUMED. It is not a verified price.",
    "RiskBudget.paper_equity_sol defaults to 10 in code. That number is not this "
    "desk's capital. This terminal does not copy it into the ledger or the snapshot.",
    "ticket, state autonomy, state halt --clear, state resume, and killswitch clear "
    "still call ensure_paper_genesis. On an empty canonical ledger they write "
    "initial_equity_lamports from that code default.",
    "state status, killswitch set, killswitch status, and killswitch flatten still "
    "open the canonical ledger through the operator registry, which writes the same "
    "default onto an empty ledger.",
    "run --mode paper --once --replay writes var/replay/<dataset>/ledger.sqlite, "
    "stamps the code-default equity there, and can record simulated fills in that "
    "sandbox. Those rows are fixture replay, not desk performance.",
    "This process cannot sign, broadcast, or place orders.",
    "Live mode is not started from this terminal.",
    "This package does not create bots or a Trading Floor group.",
)

_EVENT_SQL = (
    "SELECT seq, ts_utc, source, correlation_id, idempotency_key,"
    " type, payload_json, prev_hash, hash FROM events ORDER BY seq ASC"
)

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PumpGrok paper terminal</title>
<meta http-equiv="refresh" content="5">
<meta name="referrer" content="no-referrer">
</head>
<body>
<h1>PumpGrok paper terminal</h1>
<p>Read only. This page cannot sign, broadcast, or place orders. POST is refused.</p>
<p>Idempotency keys are unique in the append-only ledger. This page does not append.</p>
<p>The initial_equity_lamports meta row is not covered by the event hash chain.</p>
<h2>Ledger</h2>
<ul>
<li>Presence: {presence}</li>
<li>Chain: {chain}</li>
<li>Events: {events}</li>
<li>Last seq: {last_seq}</li>
<li>Last timestamp: {last_ts}</li>
</ul>
<h2>Capital and risk</h2>
<ul>
<li>Initial equity meta: {capital_meta}</li>
<li>Initial equity lamports: {capital_lamports}</li>
<li>Human capital record: {human_capital}</li>
<li>Capital interpretation: {capital_meaning}</li>
<li>Risk limits: {risk}</li>
<li>Autonomy: {autonomy}</li>
</ul>
<h2>Folded state</h2>
<ul>
<li>Equity: {equity}</li>
<li>Cash: {cash}</li>
<li>Positions: {positions}</li>
<li>Halt: {halt}</li>
<li>Kill switch from ledger rows: {kill}</li>
<li>PositionClosed rows: {closes}</li>
<li>P and L: {pnl}</li>
</ul>
<h2>Tickets</h2>
{tickets}
<h2>Recent ledger events</h2>
{event_table}
<h2>Execution</h2>
<ul>
<li>Paper CLI: unavailable</li>
<li>Scout pipeline: not_implemented</li>
<li>Signs: false</li>
<li>Broadcasts: false</li>
<li>Places orders: false</li>
</ul>
<h2>Blockers</h2>
{blockers}
<p>JSON snapshot: /terminal.json</p>
</body>
</html>
"""


@dataclass(frozen=True)
class _Loaded:
    """One read-only look at a ledger path."""

    presence: str
    chain: str
    events: tuple[dict[str, Any], ...]
    meta: dict[str, str] | None
    error: str


def _ro_uri(path: Path) -> str:
    return "file:" + quote(path.resolve().as_posix()) + "?mode=ro"


def _missing(error: str = "") -> _Loaded:
    return _Loaded("UNAVAILABLE", "UNAVAILABLE", (), None, error)


def _read_meta(db: sqlite3.Connection) -> dict[str, str]:
    rows = db.execute("SELECT key, value FROM meta").fetchall()
    return {str(key): str(value) for key, value in rows}


def _read_events(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(_EVENT_SQL).fetchall()
    events: list[dict[str, Any]] = []
    for seq, ts_utc, source, cid, key, etype, payload_json, prev_hash, hash_ in rows:
        payload: dict[str, Any] | None
        try:
            parsed = json.loads(payload_json)
        except ValueError:
            payload = None
        else:
            payload = parsed if isinstance(parsed, dict) else None
        events.append(
            {
                "seq": int(seq),
                "ts_utc": str(ts_utc),
                "source": str(source),
                "correlation_id": str(cid),
                "idempotency_key": str(key),
                "type": str(etype),
                "payload": payload,
                "payload_json": str(payload_json),
                "prev_hash": str(prev_hash),
                "hash": str(hash_),
            }
        )
    return events


def _chain_status(events: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    expected = GENESIS_PREV_HASH
    for event in events:
        if event["prev_hash"] != expected:
            return "INCONSISTENT"
        recomputed = _row_hash(
            prev_hash=str(event["prev_hash"]),
            seq=int(event["seq"]),
            ts_utc=str(event["ts_utc"]),
            source=str(event["source"]),
            correlation_id=str(event["correlation_id"]),
            idempotency_key=str(event["idempotency_key"]),
            type=str(event["type"]),
            payload_json=str(event["payload_json"]),
        )
        if recomputed != event["hash"]:
            return "INCONSISTENT"
        expected = str(event["hash"])
    return "CONSISTENT"


def load_ledger(path: Path) -> _Loaded:
    """Read ``path`` without creating it. Missing and unreadable stay distinct."""
    if not path.exists():
        return _missing()
    if not path.is_file():
        return _Loaded("UNREADABLE", "UNREADABLE", (), None, "path is not a file")
    try:
        db = sqlite3.connect(_ro_uri(path), uri=True)
    except sqlite3.Error as exc:
        return _Loaded("UNREADABLE", "UNREADABLE", (), None, type(exc).__name__)
    try:
        try:
            meta = _read_meta(db)
            events = _read_events(db)
        except sqlite3.Error as exc:
            return _Loaded("PRESENT", "UNREADABLE", (), None, type(exc).__name__)
    finally:
        db.close()
    return _Loaded("PRESENT", _chain_status(events), tuple(events), meta, "")


def _parse_equity(meta: dict[str, str] | None) -> tuple[str, int | None]:
    if meta is None:
        return "UNAVAILABLE", None
    if "initial_equity_lamports" not in meta:
        return "ABSENT", None
    raw = meta["initial_equity_lamports"].strip()
    leading = ""
    digits = raw
    if raw.startswith("-"):
        leading = "-"
        digits = raw[1:]
    if digits == "" or not digits.isdigit():
        return "UNREADABLE", None
    return "PRESENT", int(leading + digits)


def _payloads(events: tuple[dict[str, Any], ...], etype: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if event.get("type") == etype and isinstance(payload, dict):
            found.append(payload)
    return found


def _autonomy(events: tuple[dict[str, Any], ...], chain: str) -> dict[str, Any]:
    if chain != "CONSISTENT":
        return {"status": "UNAVAILABLE", "policy": None}
    policy: str | None = None
    for payload in _payloads(events, "ConfigChanged"):
        keys = payload.get("keys_changed")
        new = payload.get("new_hash")
        by = payload.get("by")
        if not isinstance(keys, list) or "autonomy_level" not in keys:
            continue
        if new not in _AUTONOMY_LEVELS or not str(by or "").startswith("human:"):
            continue
        policy = str(new)
    if policy is None:
        return {"status": "UNCONFIGURED", "policy": None}
    return {"status": "RECORDED", "policy": policy}


def _risk(events: tuple[dict[str, Any], ...], chain: str) -> dict[str, Any]:
    if chain != "CONSISTENT":
        return {
            "status": "UNAVAILABLE",
            "human_keys": [],
            "code_defaults_applied_to_this_view": False,
        }
    keys: list[str] = []
    for payload in _payloads(events, "ConfigChanged"):
        changed = payload.get("keys_changed")
        if not isinstance(changed, list):
            continue
        for key in changed:
            text = str(key)
            if text != "autonomy_level" and text not in keys:
                keys.append(text)
    status = "RECORDED" if keys else "UNCONFIGURED"
    return {
        "status": status,
        "human_keys": keys,
        "code_defaults_applied_to_this_view": False,
    }


def _capital(loaded: _Loaded) -> dict[str, Any]:
    meta_status, lamports = _parse_equity(loaded.meta)
    if loaded.chain != "CONSISTENT":
        return {
            "meta_status": meta_status if loaded.presence == "PRESENT" else "UNAVAILABLE",
            "initial_equity_lamports": None,
            "human_capital_record": "UNAVAILABLE",
            "interpretation": "UNAVAILABLE",
            "meta_in_hash_chain": False,
        }
    human = "ABSENT"
    for payload in _payloads(loaded.events, "ConfigChanged"):
        changed = payload.get("keys_changed")
        if not isinstance(changed, list):
            continue
        if any("equity" in str(key) or "capital" in str(key) for key in changed):
            human = "PRESENT"
    if meta_status == "ABSENT":
        meaning = "UNCONFIGURED"
    elif meta_status == "PRESENT":
        meaning = "RECORDED_META_ONLY"
    else:
        meaning = "UNAVAILABLE"
    return {
        "meta_status": meta_status,
        "initial_equity_lamports": lamports,
        "human_capital_record": human,
        "interpretation": meaning,
        "meta_in_hash_chain": False,
    }


def _ticket_view(loaded: _Loaded) -> dict[str, Any]:
    if loaded.chain != "CONSISTENT":
        return {"status": "UNAVAILABLE", "ids": None}
    ids: list[str] = []
    for payload in _payloads(loaded.events, "JournalNote"):
        if payload.get("note_kind") != "ticket":
            continue
        refs = payload.get("refs")
        if isinstance(refs, list) and refs:
            ids.append(str(refs[0]))
    if not ids:
        return {"status": "NONE", "ids": []}
    return {"status": "PRESENT", "ids": ids}


def _recent(loaded: _Loaded) -> dict[str, Any]:
    if loaded.chain != "CONSISTENT":
        return {"status": "UNAVAILABLE", "rows": None}
    tail = loaded.events[-_RECENT_LIMIT:]
    if not tail:
        return {"status": "NONE", "rows": []}
    rows = [
        {
            "seq": event["seq"],
            "ts_utc": event["ts_utc"],
            "source": event["source"],
            "type": event["type"],
            "idempotency_key": event["idempotency_key"],
        }
        for event in tail
    ]
    return {"status": "PRESENT", "rows": rows}


def _closed_rows(loaded: _Loaded) -> dict[str, Any]:
    if loaded.chain != "CONSISTENT":
        return {"status": "UNAVAILABLE", "count": None}
    count = sum(1 for event in loaded.events if event["type"] == "PositionClosed")
    return {"status": "KNOWN", "count": count}


def _unavailable_fold() -> dict[str, Any]:
    return {
        "equity": {"status": "UNAVAILABLE", "lamports": None},
        "cash": {"status": "UNAVAILABLE", "lamports": None},
        "positions": {"status": "UNAVAILABLE", "open_count": None, "mints": None},
        "halt": {"status": "UNAVAILABLE", "reason": None},
        "kill_switch": {"status": "UNAVAILABLE"},
    }


def _fold(loaded: _Loaded, genesis: int | None) -> dict[str, Any]:
    """Fold positions always. Fold money only when genesis was recorded."""
    if loaded.chain != "CONSISTENT":
        return _unavailable_fold()
    base = 0 if genesis is None else genesis
    try:
        state = initial_state(source="paper", initial_equity_lamports=base, day_utc="")
        for event in loaded.events:
            if not isinstance(event.get("payload"), dict):
                raise ValueError("payload")
            state = reduce(state, event)
    except (PumpgrokError, ValueError, TypeError, OverflowError):
        return _unavailable_fold()
    open_positions = positions(state)
    mints = sorted(open_positions)
    position_view: dict[str, Any]
    if mints:
        position_view = {"status": "OPEN", "open_count": len(mints), "mints": mints}
    else:
        position_view = {"status": "NONE", "open_count": 0, "mints": []}
    halt_status = "ACTIVE" if state.halt.active else "INACTIVE"
    kill_status = "ACTIVE" if state.kill_switch.active else "INACTIVE"
    equity: dict[str, Any]
    cash: dict[str, Any]
    if genesis is None:
        equity = {"status": "UNCONFIGURED", "lamports": None}
        cash = {"status": "UNCONFIGURED", "lamports": None}
    else:
        equity = {"status": "FOLDED", "lamports": equity_lamports(state)}
        cash = {"status": "FOLDED", "lamports": cash_lamports(state)}
    return {
        "equity": equity,
        "cash": cash,
        "positions": position_view,
        "halt": {"status": halt_status, "reason": state.halt.reason or None},
        "kill_switch": {"status": kill_status},
    }


def _ledger_view(path: Path, loaded: _Loaded) -> dict[str, Any]:
    last = loaded.events[-1] if loaded.events else None
    count: int | None
    if loaded.presence == "PRESENT" and loaded.chain in {"CONSISTENT", "INCONSISTENT"}:
        count = len(loaded.events)
    else:
        count = None
    return {
        "path": str(path),
        "presence": loaded.presence,
        "chain": loaded.chain,
        "event_count": count,
        "last_seq": None if last is None else last["seq"],
        "last_ts_utc": None if last is None else last["ts_utc"],
        "error": loaded.error or None,
    }


def _execution() -> dict[str, Any]:
    return {
        "paper_buy": "unavailable",
        "paper_sell": "unavailable",
        "scout_pipeline": "not_implemented",
        "curve_price_source": "unverified",
        "signs": False,
        "broadcasts": False,
        "places_orders": False,
        "creates_ledger": False,
    }


def build_snapshot(ledger_path: Path, *, requested_mode: str | None = None) -> dict[str, Any]:
    """JSON-ready snapshot. Does not create ``ledger_path`` or stamp equity."""
    loaded = load_ledger(ledger_path)
    _meta_status, genesis = _parse_equity(loaded.meta)
    if loaded.chain != "CONSISTENT":
        genesis = None
    folded = _fold(loaded, genesis if _meta_status == "PRESENT" else None)
    blockers = list(BLOCKERS)
    if requested_mode == "live":
        blockers.append("Requested mode is live. This terminal does not start live execution.")
    return {
        "viewer": {
            "signs": False,
            "broadcasts": False,
            "places_orders": False,
            "creates_ledger": False,
            "bind_policy": "127.0.0.1-only",
        },
        "requested_mode": requested_mode,
        "ledger": _ledger_view(ledger_path, loaded),
        "capital": _capital(loaded),
        "risk_limits": _risk(loaded.events, loaded.chain),
        "autonomy": _autonomy(loaded.events, loaded.chain),
        "equity": folded["equity"],
        "cash": folded["cash"],
        "positions": folded["positions"],
        "halt": folded["halt"],
        "kill_switch": folded["kill_switch"],
        "tickets": _ticket_view(loaded),
        "recent_events": _recent(loaded),
        "position_closed_rows": _closed_rows(loaded),
        "pnl": {
            "status": "UNAVAILABLE",
            "reason": "this terminal does not compute a performance figure",
        },
        "execution": _execution(),
        "blockers": blockers,
    }


def _esc(value: object) -> str:
    return escape(str(value), quote=True)


def _show_count(status: str, count: object) -> str:
    if status in {"UNAVAILABLE", "UNCONFIGURED"} or count is None:
        return status
    return f"{status} {count}"


def _show_events(ledger: Mapping[str, Any]) -> str:
    count = ledger.get("event_count")
    if ledger.get("presence") != "PRESENT" or count is None:
        return "UNAVAILABLE"
    return f"COUNT {count}"


def _show_last(presence: str, value: object) -> str:
    if presence != "PRESENT":
        return "UNAVAILABLE"
    if value is None:
        return "NONE"
    return str(value)


def _show_autonomy(autonomy: Mapping[str, Any]) -> str:
    if autonomy.get("status") == "RECORDED":
        policy = autonomy.get("policy")
        return str(policy) if policy is not None else "RECORDED"
    status = autonomy.get("status")
    return str(status) if status is not None else "UNAVAILABLE"


def _ticket_html(tickets: Mapping[str, Any]) -> str:
    status = str(tickets.get("status"))
    ids = tickets.get("ids")
    if status != "PRESENT" or not isinstance(ids, list):
        return f"<p>{_esc(status)}</p>"
    items = "".join(f"<li>{_esc(item)}</li>" for item in ids)
    return "<ul>" + items + "</ul>"


def _event_html(recent: Mapping[str, Any]) -> str:
    status = str(recent.get("status"))
    rows = recent.get("rows")
    if status != "PRESENT" or not isinstance(rows, list):
        return f"<p>{_esc(status)}</p>"
    body = "".join(
        "<tr><td>{seq}</td><td>{ts}</td><td>{typ}</td><td>{src}</td><td>{key}</td></tr>".format(
            seq=_esc(row.get("seq")),
            ts=_esc(row.get("ts_utc")),
            typ=_esc(row.get("type")),
            src=_esc(row.get("source")),
            key=_esc(row.get("idempotency_key")),
        )
        for row in rows
        if isinstance(row, dict)
    )
    return (
        "<table><thead><tr><th>seq</th><th>ts_utc</th><th>type</th>"
        "<th>source</th><th>idempotency_key</th></tr></thead><tbody>" + body + "</tbody></table>"
    )


def _blocker_html(blockers: object) -> str:
    if not isinstance(blockers, list):
        return "<p>UNAVAILABLE</p>"
    return "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in blockers) + "</ul>"


def render_terminal_html(snapshot: Mapping[str, Any]) -> str:
    """ASCII HTML for one snapshot. Ledger strings are escaped."""
    ledger = snapshot["ledger"]
    capital = snapshot["capital"]
    risk = snapshot["risk_limits"]
    autonomy = snapshot["autonomy"]
    positions = snapshot["positions"]
    halt = snapshot["halt"]
    closes = snapshot["position_closed_rows"]
    assert isinstance(ledger, dict)
    assert isinstance(capital, dict)
    assert isinstance(risk, dict)
    assert isinstance(autonomy, dict)
    assert isinstance(positions, dict)
    assert isinstance(halt, dict)
    assert isinstance(closes, dict)
    equity = snapshot["equity"]
    cash = snapshot["cash"]
    assert isinstance(equity, dict)
    assert isinstance(cash, dict)
    return _PAGE.format(
        presence=_esc(ledger.get("presence")),
        chain=_esc(ledger.get("chain")),
        events=_esc(_show_events(ledger)),
        last_seq=_esc(_show_last(str(ledger.get("presence")), ledger.get("last_seq"))),
        last_ts=_esc(_show_last(str(ledger.get("presence")), ledger.get("last_ts_utc"))),
        capital_meta=_esc(capital.get("meta_status")),
        capital_lamports=_esc(
            _show_count(str(capital.get("interpretation")), capital.get("initial_equity_lamports"))
        ),
        human_capital=_esc(capital.get("human_capital_record")),
        capital_meaning=_esc(capital.get("interpretation")),
        risk=_esc(risk.get("status")),
        autonomy=_esc(_show_autonomy(autonomy)),
        equity=_esc(_show_count(str(equity.get("status")), equity.get("lamports"))),
        cash=_esc(_show_count(str(cash.get("status")), cash.get("lamports"))),
        positions=_esc(_show_count(str(positions.get("status")), positions.get("open_count"))),
        halt=_esc(halt.get("status")),
        kill=_esc(snapshot["kill_switch"].get("status")),
        closes=_esc(_show_count(str(closes.get("status")), closes.get("count"))),
        pnl=_esc(snapshot["pnl"].get("status")),
        tickets=_ticket_html(snapshot["tickets"]),
        event_table=_event_html(snapshot["recent_events"]),
        blockers=_blocker_html(snapshot.get("blockers")),
    )


def _send(handler: BaseHTTPRequestHandler, code: int, content_type: str, body: bytes) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "default-src 'none'")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.end_headers()
    handler.wfile.write(body)


def _handle_get(handler: BaseHTTPRequestHandler, ledger_path: Path) -> None:
    path = urlsplit(handler.path).path
    snapshot = build_snapshot(ledger_path)
    if path == "/terminal.json":
        body = json.dumps(snapshot).encode("utf-8")
        _send(handler, 200, "application/json", body)
        return
    if path in {"/", "/index.html"}:
        body = render_terminal_html(snapshot).encode("utf-8")
        _send(handler, 200, "text/html; charset=utf-8", body)
        return
    _send(handler, 404, "application/json", b'{"ok":false,"code":"not_found"}')


def _reject_write(handler: BaseHTTPRequestHandler) -> None:
    body = b'{"ok":false,"code":"method_not_allowed","message":"terminal is read only"}'
    _send(handler, 405, "application/json", body)


def _handler_for(ledger_path: Path) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _handle_get(self, ledger_path)

        def do_POST(self) -> None:
            _reject_write(self)

        def do_PUT(self) -> None:
            _reject_write(self)

        def do_DELETE(self) -> None:
            _reject_write(self)

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("terminal %s", args[0] if args else fmt)

    return _Handler


def bind_terminal(ledger_path: Path, port: int) -> ThreadingHTTPServer:
    """Bind the read-only terminal on 127.0.0.1. The host is a literal."""
    return ThreadingHTTPServer((LOOPBACK_HOST, port), _handler_for(ledger_path))


def serve_bound(server: ThreadingHTTPServer) -> int:
    """Serve one already-bound terminal until keyboard interrupt."""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
