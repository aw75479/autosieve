# Security

This document describes the security considerations for mailfilter.

## Threat Model

mailfilter handles email credentials (IMAP and ManageSieve passwords) and
generates Sieve scripts that are uploaded to mail servers.  The primary threats
are:

1. **Credential leakage** -- passwords appearing in logs, files, or process
   listings.
2. **Sieve injection** -- untrusted alias values producing malformed or
   malicious Sieve scripts.
3. **Man-in-the-middle** -- connections to IMAP or ManageSieve servers being
   intercepted.
4. **Malicious configuration** -- crafted JSON or TOML config files causing
   unexpected behavior.

## Mitigations

### Credential handling

- Passwords are **never logged** or written to output files.
- Interactive password entry uses `getpass.getpass()` which disables terminal
  echo.
- The `--password` CLI flag is available for non-interactive use (e.g. CI
  pipelines).  In that case the password may appear in the process argument
  list.  For sensitive environments, prefer entering the password interactively
  or storing it in the system keyring.
- **Keyring support** (optional): install `keyring` (`pip install mailfilter[keyring]`)
  to store and retrieve passwords from the OS keychain (macOS Keychain,
  Windows Credential Vault, Linux Secret Service / KWallet).  Use
  `--store-password` to save a password to the keyring on first use.
- `mailfilter.toml` is listed in `.gitignore` by default so credentials are
  not accidentally committed.

### Authentication limitations

Only **SASL PLAIN** (ManageSieve) and **IMAP LOGIN** are supported.  These
mechanisms send credentials over the wire, which is safe when used with TLS
(`connection_security = "auto"`, `"ssl"`, or `"starttls"`).

More advanced mechanisms supported by Thunderbird and other clients are
**not** implemented:

- CRAM-MD5, SCRAM-SHA-1/256 (challenge-response)
- NTLM, Kerberos / GSSAPI
- OAuth2 (XOAUTH2, OAUTHBEARER) -- required by Gmail and Microsoft 365

For servers that require OAuth2, mailfilter cannot be used directly.

**Never use PLAIN/LOGIN without TLS** -- credentials would be sent in clear
text.  `connection_security = "none"` disables encryption entirely and should
only be used on trusted local networks.

### Sieve injection prevention

Alias values and folder names are embedded in Sieve scripts as quoted strings.
The `sieve_quote()` function escapes the two characters that are significant
inside Sieve quoted strings:

- **Backslash** `\` is escaped to `\\`.
- **Double quote** `"` is escaped to `\"`.

This is the complete set of escapes required by RFC 5228 section 2.4.2 for
quoted strings.  No other characters (including newlines, NUL, or Unicode) can
break out of a Sieve quoted string context.

**No further sanitization is required** because:

- Sieve scripts are not executed in a shell context.
- Sieve quoted strings do not support variable interpolation.
- Folder names (`fileinto` arguments) are interpreted by the mail server as
  mailbox names, not as commands.

### Transport security

- IMAP and ManageSieve connections both default to
  `connection_security = "auto"`.
- `auto` tries the most secure option first and falls back as needed.
- TLS certificate verification is **always enabled** for TLS modes.
- SSL contexts use `ssl.create_default_context()` which enforces modern TLS
  settings (TLS 1.2+, strong cipher suites, hostname verification).

### Configuration parsing

- JSON parsing uses Python's `json.load()` from the standard library, which
  is safe against code execution attacks.
- TOML parsing uses Python 3.11+'s `tomllib.load()`, equally safe.
- All configuration values are validated and type-checked before use.
  Invalid values raise `ConfigError` with descriptive messages.
- Numeric fields (ports, limits) are cast to `int`, preventing type confusion.

### File system safety

- Output files are written with explicit encoding (`utf-8`) and newline
  control.
- No shell commands or subprocess calls are made.
- File paths come from CLI arguments or configuration, not from email content.
  Alias values are only used as Sieve string values, never as file paths.

## Recommendations for operators

1. **Protect `mailfilter.toml`** with restrictive file permissions
   (`chmod 600`) if it contains passwords.
2. **Use the system keyring** (`--store-password`) instead of storing
   passwords in `mailfilter.toml` when possible.
3. **Use TLS security modes** (`"auto"`, `"ssl"`, or `"starttls"`) for production mail
   servers.
4. **Avoid `connection_security = "none"`** in production.  It disables
  encryption entirely.
5. **Review generated Sieve scripts** before uploading to production servers,
   especially after the first run or when aliases change significantly.
   Use `--dry-run` to preview changes before writing.
6. **Use incremental fetches** (`last_fetched`) to limit IMAP scanning to
   recent messages, reducing exposure time for the IMAP connection.
