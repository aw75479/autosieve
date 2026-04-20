# mailfilter

Generate Sieve scripts from JSON alias mappings, extract email aliases from IMAP inboxes, and upload via ManageSieve.

**Use case**: You have many email aliases (`alias@company.com`) all forwarded to one inbox. The original alias is visible in `To:`, `Delivered-To:`, `X-Original-To:`, or `Received: ... for <alias>` headers. This tool discovers those aliases and generates Sieve rules to sort them into folders.

## Installation

```bash
uv sync
```

## Usage

### 1. Extract aliases from IMAP

Scan your inbox to discover all aliases used:

```bash
mailfilter extract-aliases mail.example.com \
    --user you@example.com \
    --domain example.com \
    --since 2025-01-01 \
    --output aliases.json
```

Missing mandatory parameters (`server`, `--user`, `--domain`) are prompted interactively when omitted.

This produces a JSON file with one rule per discovered alias, all mapped to `INBOX` by default. Edit the file to group aliases and set proper folder names.

Options:

| Flag | Description |
|------|-------------|
| `server` | IMAP server as host or host:port (positional, prompted if omitted) |
| `--user` | IMAP username (prompted if omitted) |
| `--domain` | Only extract aliases matching this domain (prompted if omitted) |
| `--folder` | IMAP folder to scan (default: INBOX) |
| `--limit` | Scan at most N messages (most recent first) |
| `--since` | Only scan messages from this date (YYYY-MM-DD) |
| `--headers` | Headers to scan (default: To Delivered-To X-Original-To) |
| `--default-folder` | Default folder in generated mapping (default: INBOX) |
| `--output` | Write JSON here (default: stdout) |
| `--tls-mode` | implicit / starttls / plain (default: implicit) |
| `--password` | Password (prefer --password-file or --password-env) |
| `--password-env` | Read password from environment variable |
| `--password-file` | Read password from file |
| `--insecure` | Disable TLS certificate verification |

### 2. Edit the alias mapping

Review and edit `aliases.json` -- group aliases, set meaningful folder names:

```json
{
  "script_name": "alias-router",
  "headers": ["X-Original-To", "Delivered-To"],
  "use_create": true,
  "match_type": "is",
  "rules": [
    {
      "aliases": ["client-a@company.com", "client-a+billing@company.com"],
      "folder": "Clients/ClientA",
      "comment": "Client A correspondence"
    },
    {
      "alias": "newsletter@company.com",
      "folder": "Newsletters"
    }
  ]
}
```

### 3. Generate Sieve script

```bash
mailfilter generate aliases.json --output filter.sieve
```

### 4. Upload via ManageSieve

```bash
mailfilter generate aliases.json \
    --upload \
    --host mail.example.com:4190 \
    --username you@example.com \
    --password-file ~/.mail-password
```

Missing `--host` and `--username` are prompted interactively when `--upload` is used.

Options for `generate`:

| Flag | Description |
|------|-------------|
| `config` | JSON config file (positional, required) |
| `--output` | Write script here (default: stdout) |
| `--script-name` | Override script name from config |
| `--upload` | Upload via ManageSieve after generation |
| `--host` | ManageSieve host[:port] (default port: 4190, prompted if omitted) |
| `--username` | ManageSieve username (prompted if omitted) |
| `--tls-mode` | auto / starttls / implicit / plain |
| `--no-activate` | Upload but do not activate |
| `--no-check` | Skip CHECKSCRIPT before upload |
| `--password` | Password (prefer --password-file or --password-env) |
| `--password-env` | Read password from environment variable |
| `--password-file` | Read password from file |
| `--insecure` | Disable TLS certificate verification |

## Config format

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `script_name` | string | `alias-router` | ManageSieve script name |
| `headers` | string[] | `["X-Original-To", "Delivered-To"]` | Headers to match against |
| `use_create` | bool | `false` | Use `fileinto :create` (auto-create folders) |
| `explicit_keep` | bool | `false` | Append `keep;` at end of script |
| `match_type` | `is` or `contains` | `is` | Sieve match type |
| `rules` | list or dict | required | Alias-to-folder mappings |

Each rule: `alias` (string) and/or `aliases` (string[]), `folder` (string, required), `comment` (string, optional).

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## License

MIT
