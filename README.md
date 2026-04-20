# mailfilter

Generate Sieve scripts from JSON alias mappings, extract email aliases from IMAP inboxes, and upload via ManageSieve.

**Use case**: You have many email aliases (`alias@company.com`) all forwarded to one inbox. The original alias is visible in `To:`, `Delivered-To:`, `X-Original-To:`, or `Received: ... for <alias>` headers. This tool discovers those aliases and generates Sieve rules to sort them into folders.

## Quick start

```bash
uv sync
cp mailfilter.template.toml mailfilter.toml   # edit with your server details
uv run mailfilter extract-aliases              # scan IMAP and write aliases.json
uv run mailfilter generate aliases.json        # generate mailfilter.sieve
```

## Configuration

Copy the template and edit:

```bash
cp mailfilter.template.toml mailfilter.toml
```

`mailfilter.toml` is **auto-loaded** from the current directory. Use `--config path/to/other.toml` to override.

```toml
[imap]
host = "mail.example.com"
user = "you@example.com"
domain = "example.com"
connection_security = "ssl"      # ssl | starttls | none (aligned with Thunderbird)
folders = ["INBOX"]              # IMAP folders to scan (supports multiple)
# incremental = true             # use last_fetched for incremental scanning

[managesieve]
host = "mail.example.com"
username = "you@example.com"
connection_security = "auto"     # auto | ssl | starttls | none
folder_prefix = "alias"          # aliases sorted into <prefix>/<local-part>
# authz_id = ""                  # SASL authorization identity (RFC 4616)

[filenames]
sieve_file = "mailfilter.sieve"  # default output for 'generate'
alias_file = "aliases.json"      # default output for 'extract-aliases'
```

CLI flags override config values; missing mandatory values are prompted interactively.

## Usage

### 1. Extract aliases from IMAP

Scan your inbox to discover all aliases used:

```bash
uv run mailfilter extract-aliases
```

Or with explicit parameters:

```bash
uv run mailfilter extract-aliases mail.example.com \
    --user you@example.com \
    --domain example.com \
    --since 2025-01-01 \
    aliases.json
```

A progress bar is shown on stderr during scanning.

**Header auto-discovery**: For each alias, the tool records which headers it was found in (e.g. `X-Original-To`, `Delivered-To`). The generated Sieve rules then only match against those specific headers, making rules more precise.

**Incremental updates**: When the output alias-file already exists, only messages since the last fetch are scanned. The `last_fetched` date is stored in the alias-file.

Options:

| Flag | Description |
|------|-------------|
| `server` | IMAP server as host or host:port (positional, from config or prompted) |
| `alias-file` | JSON alias file to write/update (positional, default: `aliases.json`) |
| `--config` | Server config TOML file (default: auto-load `mailfilter.toml`) |
| `--user` | IMAP username (prompted if omitted) |
| `--domain` | Only extract aliases matching this domain (prompted if omitted) |
| `--folder` | IMAP folder(s) to scan (default: INBOX). May specify multiple. |
| `--limit` | Scan at most N messages (most recent first) |
| `--since` | Only scan messages from this date (YYYY-MM-DD) |
| `--headers` | Headers to scan (default: To Delivered-To X-Original-To) |
| `--folder-prefix` | Folder prefix for alias rules (default: alias) |
| `--connection-security` | ssl / starttls / none (default: ssl) |
| `--no-incremental` | Disable incremental scanning (ignore last_fetched) |
| `--dry-run` | Show what would change without writing |
| `--password` | Password (prompted if omitted) |
| `--store-password` | Store password in system keyring for future use |
| `--insecure` | Disable TLS certificate verification |
| `--stdout` | Write to stdout instead of a file |

### 2. Edit the alias file

Review and edit `aliases.json` -- group aliases, set meaningful folder names:

```json
{
  "script_name": "alias-router",
  "headers": ["X-Original-To", "Delivered-To"],
  "use_create": true,
  "match_type": "is",
  "last_fetched": "2025-04-20",
  "rules": [
    {
      "aliases": ["client-a@company.com", "client-a+billing@company.com"],
      "folder": "Clients/ClientA",
      "headers": ["X-Original-To"],
      "comment": "Client A correspondence"
    },
    {
      "alias": "newsletter@company.com",
      "folder": "Newsletters"
    }
  ]
}
```

Per-rule `headers` override the global setting. Rules without `headers` match against the global headers list.

### 3. Generate Sieve script

```bash
uv run mailfilter generate aliases.json
```

Output is written to `mailfilter.sieve` by default (configurable in `[filenames]`). Use `--stdout` to print to stdout, or `--output custom.sieve` to specify a file.

### 4. Upload via ManageSieve

```bash
uv run mailfilter generate aliases.json --upload
```

Missing `--host` and `--username` are prompted interactively when `--upload` is used.

Options for `generate`:

| Flag | Description |
|------|-------------|
| `alias-file` | JSON alias file (positional, required) |
| `--config` | Server config TOML file (default: auto-load `mailfilter.toml`) |
| `--output` | Output file (default: `mailfilter.sieve`) |
| `--stdout` | Write to stdout instead of a file |
| `--script-name` | Override script name from alias file |
| `--upload` | Upload via ManageSieve after generation |
| `--host` | ManageSieve host[:port] (default port: 4190, prompted if omitted) |
| `--username` | ManageSieve username (prompted if omitted) |
| `--connection-security` | auto / ssl / starttls / none |
| `--no-activate` | Upload but do not activate |
| `--no-check` | Skip CHECKSCRIPT before upload |
| `--dry-run` | Show diff of what would change without writing |
| `--password` | Password (prompted if omitted) |
| `--store-password` | Store password in system keyring for future use |
| `--insecure` | Disable TLS certificate verification |

## Alias file format

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `script_name` | string | `alias-router` | ManageSieve script name |
| `headers` | string[] | `["X-Original-To", "Delivered-To"]` | Global headers to match against |
| `use_create` | bool | `false` | Use `fileinto :create` (auto-create folders) |
| `explicit_keep` | bool | `false` | Append `keep;` at end of script |
| `match_type` | `is`, `contains`, `matches`, or `regex` | `is` | Sieve match type (`regex` requires server support) |
| `generation_mode` | `header` or `envelope` | `header` | `header`: per-alias if-blocks; `envelope`: compact envelope+variables routing |
| `folder_prefix` | string | `alias` | Folder prefix for envelope mode (`fileinto "{prefix}/${alias}"`) |
| `catch_all_folder` | string or null | `null` | Catch-all folder for unmatched aliases in envelope mode |
| `last_fetched` | string | - | ISO date of last IMAP scan (auto-managed) |
| `rules` | list or dict | required | Alias-to-folder mappings |

Each rule: `alias` (string) and/or `aliases` (string[]), `folder` (string, required), `comment` (string, optional), `headers` (string[], optional per-rule override), `active` (bool, default `true` -- set to `false` to preserve but exclude from Sieve generation).

### Envelope mode

Set `"generation_mode": "envelope"` for a compact script using Sieve `envelope` + `variables` extensions (RFC 5228, RFC 5229). Instead of one `if` block per alias, all aliases on the same domain are handled in a single block using variable interpolation:

```json
{
  "generation_mode": "envelope",
  "use_create": true,
  "folder_prefix": "alias",
  "catch_all_folder": "alias/_other",
  "rules": [
    {"alias": "alice@example.com", "folder": "alias/alice"},
    {"alias": "bob@example.com", "folder": "alias/bob"}
  ]
}
```

Generates:

```sieve
require ["fileinto", "mailbox", "envelope", "variables"];

if envelope :domain :is "to" "example.com" {
    if envelope :localpart :matches "to" "*" {
        set :lower "alias" "${1}";
        if string :is "${alias}" ["alice", "bob"] {
            fileinto :create "alias/${alias}";
            stop;
        }
        fileinto :create "alias/_other";
        stop;
    }
}
```

Requirements: server must support `envelope` and `variables` extensions (Dovecot Pigeonhole, Cyrus, Fastmail all do). The SMTP envelope `RCPT TO` must preserve the original alias (not rewrite to the final mailbox). Rules must follow the `{folder_prefix}/{local_part}` folder naming convention.

## Connection security

The `connection_security` setting aligns with Thunderbird's naming:

| Value | Description |
|-------|-------------|
| `ssl` | Implicit TLS (Thunderbird: SSL/TLS). Default for IMAP (port 993). |
| `starttls` | Upgrade plaintext to TLS (Thunderbird: STARTTLS). |
| `none` | No encryption (Thunderbird: None). |
| `auto` | Try STARTTLS if available, else plaintext. ManageSieve only. |

## Development

```bash
uv sync
uv run poe check       # full check: lint + format + typecheck + test
uv run poe format      # auto-format
uv run poe coverage    # test coverage report
```

See [docs/developer.md](docs/developer.md) for full developer guide, [docs/architecture.md](docs/architecture.md) for architecture overview, and [docs/security.md](docs/security.md) for security considerations.

## License

MIT
