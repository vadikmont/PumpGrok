"""Read-only paper terminal verb. It does not create the canonical ledger."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pumpgrok.cli.codes import EXIT_CONFIG, EXIT_OK
from pumpgrok.cli.errors import UsageError, _say
from pumpgrok.config.settings import Settings
from pumpgrok.ledger.paths import PAPER_LEDGER_PATH
from pumpgrok.reporting.terminal import LOOPBACK_HOST, bind_terminal, build_snapshot, serve_bound


def _terminal_verb(args: argparse.Namespace, settings: Settings) -> int:
    """Print or serve the ledger snapshot. Neither path places an order."""
    if args.cmd == "status":
        _say(sys.stdout, json.dumps(_status(settings), sort_keys=True))
        return EXIT_OK
    if args.host != LOOPBACK_HOST:
        _say(sys.stderr, "terminal serve binds 127.0.0.1 only")
        return EXIT_CONFIG
    if not isinstance(args.port, int) or not 0 <= args.port <= 65535:
        _say(sys.stderr, "usage error: --port must be 0-65535")
        return EXIT_CONFIG
    try:
        server = bind_terminal(PAPER_LEDGER_PATH, args.port)
    except OSError as exc:
        raise UsageError(f"terminal bind failed: {exc.strerror}") from exc
    bound = int(server.server_address[1])
    _say(sys.stdout, f"terminal listening http://{LOOPBACK_HOST}:{bound}/")
    return serve_bound(server)


def _status(settings: Settings) -> dict[str, Any]:
    """Snapshot for the canonical paper ledger, including the requested mode."""
    return build_snapshot(PAPER_LEDGER_PATH, requested_mode=settings.effective_mode)
