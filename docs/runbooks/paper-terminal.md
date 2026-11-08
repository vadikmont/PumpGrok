# Paper terminal

Read-only view of the canonical paper ledger at `var/ledger.sqlite`, relative to the working directory. The terminal does not create that file, does not stamp equity, and cannot sign, broadcast, or place an order.

## Show the snapshot

```bash
pumpgrok terminal status
```

`status` prints one JSON object and exits. A missing ledger is `presence=UNAVAILABLE`. An existing ledger with no `initial_equity_lamports` meta row is capital `UNCONFIGURED` and equity lamports null. A recorded zero is `FOLDED 0`. Those are different states. P and L is `UNAVAILABLE` from this command. The number `10` SOL in `RiskBudget` is a code schema default and is not copied into this snapshot.

## Serve the page

```bash
pumpgrok terminal serve --host 127.0.0.1 --port 8765
```

The process binds `127.0.0.1` only. Any other `--host` exits 1 and does not listen. `GET /` is HTML. `GET /terminal.json` is the same snapshot as `status`. `POST`, `PUT`, and `DELETE` return 405. Stop the process with Ctrl-C. This is a foreground process. It is not a scheduled job and it is not a public deployment.

## What this does not do

- It does not connect a wallet, store a key, or broadcast a transaction.
- It does not run discovery, a scout pipeline, or a paper buy or sell.
- It does not initialize virtual capital or risk limits.
- It does not create the ten seats or a Trading Floor group. Those stay outside this package.
- `pumpgrok paper buy` and `pumpgrok paper sell` still refuse `unavailable` because no verified curve price is bound. That refusal does not create the canonical ledger.
- `pumpgrok scout pipeline` still exits 5.
- `ticket`, `state autonomy`, `state halt --clear`, `state resume`, `killswitch clear`, `state status`, and `killswitch set|status|flatten` can still write code-default equity onto an empty canonical ledger. Do not run them until a human has chosen capital and limits on purpose.
- `run --mode paper --once --replay <dir>` writes `var/replay/<dataset>/ledger.sqlite` and can record simulated fills in that sandbox using the code-default equity. Those rows are fixture replay, not desk performance.

## Install

Install the built wheel into a Python 3.12 virtual environment with the package installer you already use. The wheel carries the `pumpgrok` console script. Check the published SHA256 before installing. No `.env` file and no RPC key are required for `terminal status` or `terminal serve`.

## Parent computer

Run the commands above on the shared computer, in the directory that should hold `var/ledger.sqlite`. A cloud agent session is not that computer. The page is reachable at `http://127.0.0.1:8765/` on the machine where the process is running.
