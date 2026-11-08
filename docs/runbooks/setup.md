# Setup runbook

First-run setup for the PumpGrok desk. The normative steps live in `SETUP.md`; this runbook is the short operator path plus the health checks.

## Quick start

```bash
uv sync
uv run pumpgrok doctor
uv run pumpgrok run --mode paper --once --replay tests/fixtures/replay/smoke
./scripts/check.sh
```

`pumpgrok doctor` reports every subsystem. `run --mode paper --once --replay` prints one engine decision (`skip` or `buy`) and appends one JournalNote. `check.sh` lints the instruction pack (skill frontmatter, constitution phrases, no network).

Copy the example environment file to `.env` before the first run and fill in the keys it names. The `.env` file itself is gitignored and is never printed, committed, or pasted anywhere.

## What setup creates

- Runtime state under `./var/` (`ledger.sqlite`, `log/`, replay ledgers under `var/replay/`), created on first run. Nothing is written outside the checkout except the log rotation target configured for the profile.
- A local `.env` file with operator-held secrets, referenced by name only.
- A synced virtualenv from `uv sync --frozen` (the lockfile is committed; the interpreter is pinned by `.python-version`).

## Rules that apply during setup

- Follow `SETUP.md` top to bottom exactly (normative). Start with the `pumpgrok-setup` skill, then `tool-connections`, then the `desk-risk-limits` interview.
- Engagement stays at `research` through setup. There is no live-capital level on this desk; `paper` is the highest level these instructions describe.
- Never request, accept, or store seed phrases or private keys. The wallet connects via human screen hand-off only.
- Ported code under `src/pumpgrok/` (tools, engine, config, CLI, ledger) is the implementation of record. No other copy is an oracle.

## References

- Normative setup: `SETUP.md`
- Desk constitution: `rules/pumpgrok-team.mdc`, skill `desk-operating-model`
- Boot order: skills `pumpgrok-setup`, `tool-connections`, `desk-risk-limits`
- Read-only ledger page: `docs/runbooks/paper-terminal.md`

## Paper-only boundary

`SETUP.md` sections 9 and 12 still mention a throwaway wallet and the words "SETUP COMPLETE". Those steps conflict with a paper-only operator boundary:

- Do not connect a wallet, store a seed, or broadcast.
- Do not report setup complete while capital is unconfigured, the curve price source is unverified, and `scout pipeline` exits 5.
- `pumpgrok terminal status` is the shared view of `var/ledger.sqlite`. It does not create the file. See `docs/runbooks/paper-terminal.md`.
- `run --mode paper --once --replay` writes a replay sandbox ledger under `var/replay/`, not the desk ledger, and can stamp the code-default equity there. Do not treat that output as desk performance or as configured capital.
- `ticket`, several `state` verbs, and `killswitch clear` can still write that code-default equity onto an empty `var/ledger.sqlite`. Do not run them during setup.
