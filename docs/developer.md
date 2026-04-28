# Developer Guide

## Prerequisites

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

```bash
git clone <repository-url>
cd autosieve
uv sync          # installs all dependencies including dev tools
```

## Project structure

```
src/autosieve/
    cli.py            # CLI entry point, argument parsing, subcommands
    config.py         # Rule/Config dataclasses, JSON loading, validation
    imap_alias.py     # IMAP alias extraction, alias-file I/O, merging
    managesieve.py    # ManageSieve (RFC 5804) client
    server_config.py  # TOML server config loader
    sieve.py          # Sieve script generation from Config rules
tests/
    conftest.py       # Shared fixtures (sample file paths)
    test_cli.py       # CLI integration tests
    test_config.py    # Config loading, normalization, merge tests
    test_imap_alias.py # IMAP extraction, alias mapping, merge tests
    test_managesieve.py # ManageSieve client tests (mocked sockets)
    test_server_config.py # TOML config loading tests
    test_sieve.py     # Sieve generation tests
docs/
    architecture.md   # Architecture overview and standards
    developer.md      # This file
    security.md       # Security considerations
    advanced-features.md # Possible future features
samples/
    sieve_alias_mapping.sample.json  # Example alias file
    generated.sample.sieve           # Example generated output
```

## Development tasks (poethepoet)

All tasks are defined in `pyproject.toml` under `[tool.poe.tasks]` and run
via `uv run poe <task>`:

| Task            | Command                                   | Description                  |
|-----------------|-------------------------------------------|------------------------------|
| `poe format`    | `ruff format src/ tests/`                 | Auto-format code             |
| `poe check-format` | `ruff format --check src/ tests/`      | Verify formatting            |
| `poe lint`      | `ruff check src/ tests/`                  | Lint with ruff               |
| `poe test`      | `pytest`                                  | Run tests                    |
| `poe typecheck` | `mypy src/`                               | Static type checking         |
| `poe coverage`  | `pytest --cov=autosieve --cov-report=term-missing` | Test coverage report |
| `poe check`     | lint + check-format + typecheck + test    | Full CI check                |

Quick iteration loop:

```bash
uv run poe format && uv run poe check
```

## Configuration

Copy the template and edit:

```bash
cp autosieve.template.toml autosieve.toml
# edit autosieve.toml with your server details
```

`autosieve.toml` is auto-loaded from the current directory when `--config`
is not specified.  It is listed in `.gitignore` to prevent committing
credentials.

## Running locally

```bash
# Extract aliases from IMAP
uv run autosieve extract-aliases

# Generate sieve script (writes to autosieve.sieve by default)
uv run autosieve generate aliases.json

# Generate and upload
uv run autosieve generate aliases.json --upload
```

## Testing conventions

- Tests use `pytest` with no external fixtures beyond the standard library
  and `unittest.mock`.
- IMAP and ManageSieve interactions are tested via mocked connections
  (`MagicMock`), not live servers.
- Sample data files in `samples/` are used by `conftest.py` fixtures.
- Security-sensitive rules (`S101`, `S106`) are disabled in test files via
  ruff per-file-ignores.

## Adding a new module

1. Create `src/autosieve/newmodule.py`.
2. Create `tests/test_newmodule.py`.
3. Import from `cli.py` if needed.
4. Run `uv run poe check` to verify.

## Code style

- Line length: 160 characters (enforced by ruff, ruler visible in VS Code).
- Python 3.11+ features are encouraged (`tomllib`, `match`, type unions).
- No runtime dependencies beyond the standard library.  `keyring` is an
  optional dependency (`pip install autosieve[keyring]`).
- Ruff rules: E, W, F, I, N, UP, B, SIM, S, T20, RUF (see `pyproject.toml`).

## Release process

1. Update `version` in `pyproject.toml`.
2. Run `uv run poe check`.
3. Commit and push.
