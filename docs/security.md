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
  or storing it in the TOML config (which should be file-permission protected
  and added to `.gitignore`).
- `mailfilter.toml` is listed in `.gitignore` by default so credentials are
  not accidentally committed.

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

- IMAP connections default to `connection_security = "ssl"` (implicit TLS on
  port 993), matching Thunderbird's default.
- ManageSieve connections default to `connection_security = "auto"`, which
  negotiates STARTTLS when the server advertises it.
- TLS certificate verification is **enabled by default**.  The `--insecure`
  flag disables verification and should only be used for testing against
  servers with self-signed certificates.
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
2. **Use `connection_security = "ssl"`** (the default) for production mail
   servers.
3. **Do not use `--insecure`** in production.
4. **Review generated Sieve scripts** before uploading to production servers,
   especially after the first run or when aliases change significantly.
5. **Use incremental fetches** (`last_fetched`) to limit IMAP scanning to
   recent messages, reducing exposure time for the IMAP connection.
