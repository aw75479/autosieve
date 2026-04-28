# Architecture

## Overview

autosieve is a command-line tool that bridges IMAP mailboxes and Sieve mail
filtering.  It follows a **pipeline architecture**: data flows through
discrete stages that can be run independently or chained together.

```
IMAP Inbox ──> extract ──> aliases.json ──> generate ──> .sieve ──> ManageSieve
   (scan)        (discover)        (edit/merge)     (render)    (script)    (upload)

IMAP Inbox ──> apply ──> IMAP folders (retroactive rule enforcement)

extract -> generate -> apply -> upload    can be run as one ``sync`` command.
```

## Layout (v0.2.0)

The package is split into three layers, each isolated for easy
add/remove of functionality without touching the rest:

```
src/autosieve/
    __init__.py
    cli.py                # argparse + the four built-in subcommands
    config.py             # alias-file model (Rule, Config, JSON loader)
    imap_alias.py         # IMAP scanning + retroactive apply
    managesieve.py        # ManageSieve client (PUT/CHECK/SET/LIST/GET/DELETE)
    sieve.py              # Sieve script emitter (header / envelope modes)
    server_config.py      # multi-target TOML schema
    commands/             # optional sub-commands -- one file per command
        sync.py
        backup.py
        restore.py
    features/             # optional sieve-affecting features
        vacation.py       # RFC 5230
        notify.py         # RFC 5435
        custom_filters.py # arbitrary header / from / subject / body filters
        oauth2.py         # XOAUTH2 token resolution
```

Modules under ``commands/`` and ``features/`` are loaded by
``cli.build_arg_parser()`` and ``cli._cmd_generate()`` via
``importlib.import_module``; deleting any of these files removes that
feature without breaking the rest of the CLI.

## Modules

### cli.py -- Command-line interface

Entry point.  Defines subcommands (`generate`, `extract`, `upload`, `apply`) via
`argparse`.  Handles parameter resolution with priority:
CLI flags > TOML config > interactive prompts.

### config.py -- Configuration model

Defines the internal data model (`Rule`, `Config`) and loads alias files
(JSON).  Responsible for:

- Normalizing two input formats (dict and list) into `list[Rule]`.
- Merging rules that target the same folder (`_merge_rules_by_folder`).
- Validating all fields with descriptive error messages.

### imap_alias.py -- IMAP alias extraction

Connects to an IMAP server, scans message headers, and discovers email
aliases.  Key design decisions:

- **Batched FETCH**: messages are fetched in batches of 100 to avoid IMAP
  argument length limits.
- **Header tracking**: each discovered alias records which headers it was
  found in (e.g. `X-Original-To`, `Delivered-To`), enabling per-rule header
  matching in generated Sieve scripts.
- **Incremental updates**: the `last_fetched` date in the alias file enables
  scanning only new messages.
- **Merge semantics**: new aliases are merged into existing alias files
  respecting folder grouping and deduplication.

### sieve.py -- Sieve script generator

Renders `Config` into a valid Sieve script (RFC 5228).  Produces:

- `require` declarations based on active features.
- One `if` block per rule with `anyof` conditions when multiple
  aliases/headers match.
- Per-rule header matching when discovered headers are available, falling
  back to global headers otherwise.

### managesieve.py -- ManageSieve client

Implements a subset of the ManageSieve protocol (RFC 5804):

- SASL PLAIN authentication.
- STARTTLS negotiation (automatic or explicit).
- `PUTSCRIPT`, `SETACTIVE`, `LISTSCRIPTS`, `CHECKSCRIPT` commands.

### server_config.py -- TOML configuration

Loads server settings from a TOML file (`autosieve.toml`).  Sections:
`[imap]`, `[managesieve]`, `[filenames]`.

## Standards reference

| Standard   | Title                                      | Usage                    |
|------------|--------------------------------------------|--------------------------|
| RFC 5228   | Sieve: An Email Filtering Language         | Script generation        |
| RFC 5229   | Sieve: Variables Extension                 | Envelope mode generation |
| RFC 5804   | ManageSieve Protocol                       | Script upload/management |
| RFC 3501   | IMAP4rev1                                  | Alias extraction         |
| RFC 5321   | SMTP (envelope-level routing)              | Received header parsing  |
| RFC 5322   | Internet Message Format                    | Address header parsing   |

## Data flow

### extract

1. Connect to IMAP server (SSL/STARTTLS/plain).
2. SELECT folder (default: INBOX).
3. SEARCH for message IDs (optionally filtered by SINCE date).
4. FETCH headers in batches of 100 (`To`, `Delivered-To`, `X-Original-To`,
   `Received`).
5. Parse addresses from each header, recording which header each alias was
   found in.
6. Filter by domain.
7. Group aliases by folder (`<prefix>/<local-part>`), merge `+` suffixes.
8. Merge into existing alias file or create new one.
9. Update `last_fetched` timestamp.
10. Write JSON output.

### generate

1. Load alias file (JSON) via `config.py`.
2. Normalize rules (dict or list format -> `list[Rule]`).
3. Merge rules targeting the same folder.
4. Render Sieve script via `sieve.py`:
   - **Header mode** (default): one `if header` block per rule.
   - **Envelope mode**: groups aliases by domain, generates compact
     `address` + `variables` script with dynamic `fileinto`.
5. Optionally upload via ManageSieve.

### apply

1. Load alias file (JSON) to get active rules.
2. Connect to IMAP server.
3. For each active rule, SEARCH source folder(s) for messages matching any alias.
4. Move matched messages to the rule's target folder (IMAP MOVE or COPY+DELETE).
5. Supports dry-run mode (count matches without moving) and optional folder creation.

## Design principles

- **Stdlib-only runtime**: no third-party dependencies at runtime.  All
  dependencies (ruff, pytest, mypy, etc.) are dev-only.
- **Separation of concerns**: each module has a single responsibility.
  `config.py` knows nothing about IMAP; `sieve.py` knows nothing about
  ManageSieve.
- **Incremental operation**: designed for repeated runs.  Alias files are
  merged, not overwritten.  Only new messages are scanned.
- **Fail-fast validation**: configuration errors are caught early with clear
  messages before any network operations.
- **Testability**: all network I/O is behind interfaces that can be mocked.
  No global state.
