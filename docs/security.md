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

---

## Risk Analysis: What Can Go Wrong with Sieve Rules

This section analyses operational risks arising from incorrect, incomplete, or
missing Sieve rules, and describes concrete mitigations.

### Risk register

| ID   | Risk                                         | Likelihood | Impact       |
|------|----------------------------------------------|------------|--------------|
| R-01 | Incorrect fileinto destination               | Medium     | Mail misrouted |
| R-02 | Missing rule — mail stays in INBOX           | Medium     | Missed sorting |
| R-03 | Silent rule deletion / deactivation          | Low        | Mail stays in INBOX |
| R-04 | ManageSieve upload fails silently            | Low        | Old script remains active |
| R-05 | Sieve script syntax error (server rejects)   | Low        | No filtering at all |
| R-06 | `fileinto :create` unavailable on server     | Medium     | Delivery failure |
| R-07 | Mail delivered to wrong alias (catch-all)    | Medium     | Privacy/confusion |
| R-08 | Server-side script overwrite without backup  | Low        | Rollback impossible |
| R-09 | Incremental scan misses old messages         | Low        | Aliases not discovered |
| R-10 | `apply` moves wrong messages                 | Low        | Permanent message loss |

### R-01 — Incorrect fileinto destination

**Scenario**: an alias is mapped to the wrong folder (e.g. typo in folder name
or after renaming an IMAP folder).

**Consequence**: mail is sorted into the wrong folder and may be overlooked.

**Mitigations**:
- Use `--dry-run` on `generate` to inspect the script before uploading.
- After upload, send a test message to each alias and verify delivery.
- Enable `use_create = true` in the alias file so autosieve creates folders
  automatically; a missing target folder then causes an obvious IMAP error
  rather than silent mis-delivery.

### R-02 — Missing rule / mail stays in INBOX

**Scenario**: a new alias starts receiving mail before the alias file has been
updated and a new rule has been generated and uploaded.

**Consequence**: mail lands in INBOX, where it may be overlooked or sorted manually.

**Mitigations**:
- Run `autosieve extract` regularly (e.g. daily via cron) to discover new
  aliases automatically.
- Use envelope mode with a `catch_all_folder` (e.g. `alias/_other`).  Mail
  to any unknown alias in the monitored domain is sorted into a visible
  catch-all folder instead of staying in INBOX.
- Monitor INBOX for unexpected domain-addressed mail.

### R-03 — Silent rule deletion / deactivation

**Scenario**: a rule is accidentally marked `"active": false` or deleted from
the alias file; the script is regenerated and uploaded without the affected rule.

**Consequence**: mail to that alias stays in INBOX instead of being sorted.

**Mitigations**:
- Keep the alias file in version control.  Review diffs before committing.
- Use `--dry-run` on `generate` to see which rules are about to be removed.
- The CLI prints a count of active and inactive rules; monitor for unexpected
  changes.

### R-04 — ManageSieve upload fails silently / old script remains active

**Scenario**: the upload step (`generate --upload` or `upload`) fails due to a
network error or authentication problem.  The old script continues to run on
the server.

**Consequence**: new aliases are never routed; the server runs stale rules.

**Mitigations**:
- The CLI always reports the exit code and any error message.  Treat a
  non-zero exit code as a deployment failure.
- After upload, check the server's active script with `autosieve upload --no-check
  --no-activate` to list existing scripts and verify the active one is current.
- Automate uploads in CI with exit-code checks (`set -e` in shell scripts).

### R-05 — Sieve script syntax error (server rejects upload)

**Scenario**: a generated script contains a syntax error (e.g. due to a
character in a folder name that the server's Sieve parser rejects).

**Consequence**: the server refuses the upload; the *previous* script remains
active.  New aliases are not routed until the problem is fixed.

**Mitigations**:
- `CHECKSCRIPT` is run automatically before upload (disable only with
  `--no-check` if the server doesn't support it).
- Review the Sieve script with a local syntax checker (e.g. `sieve-test`) as
  an extra gate in CI.
- Unusual folder names (non-ASCII, special characters) should be tested on
  the target server before use in production alias mappings.

### R-06 — `fileinto :create` unavailable

**Scenario**: the mail server does not support the `mailbox` Sieve extension
(RFC 4469), but the alias file has `use_create = true`.

**Consequence**: the server may reject the script entirely, or deliver to a
fallback location.  RFC 4469 defines rejection behaviour as server-specific.

**Mitigations**:
- Disable `use_create` if the server does not advertise the `mailbox`
  extension in its SIEVE capability list.
- Pre-create all target folders with an IMAP client before uploading the
  script.
- Use `CHECKSCRIPT` (default) to verify script acceptance before it goes live.

### R-07 — Mail delivered to wrong alias via catch-all

**Scenario**: envelope mode routes any mail addressed to the monitored domain
that doesn't match a known local-part into `catch_all_folder`.  If a
legitimate alias is missing from the rule set, its mail silently lands in the
catch-all.

**Consequence**: mail may be mixed with spam/probe messages in the catch-all
folder, reducing visibility.

**Mitigations**:
- Run `autosieve extract` regularly so new aliases are discovered promptly.
- Monitor the catch-all folder; treat unexpected mail there as a signal to
  run `extract` and regenerate the script.
- Optionally set a short incremental scan interval (daily) in an automated
  pipeline.

### R-08 — Overwriting the active script without a backup

**Scenario**: a script is uploaded and immediately activated.  The previous
script is not saved locally before overwriting.

**Consequence**: rollback requires manual reconstruction.

**Mitigations**:
- Keep `aliasfilter.sieve` in version control alongside `aliases.json`.
- Use `--no-activate` to upload a new script without activating it; verify
  it, then activate manually.
- The server typically retains old scripts under different names; `LISTSCRIPTS`
  shows them.

### R-09 — Incremental scan misses old messages

**Scenario**: the `last_fetched` date in the alias file is in the future, or
the initial scan was limited by `--limit`, causing some aliases to never be
discovered.

**Consequence**: rules are never generated for those aliases; mail stays in INBOX.

**Mitigations**:
- Run a full scan periodically (`--no-incremental`) to catch any gaps.
- Use a one-day overlap: autosieve already subtracts one day from
  `last_fetched` to avoid boundary misses.
- After the initial setup, send test messages to all known aliases and verify
  rule coverage.

### R-10 — `apply` moves wrong messages (permanent message loss risk)

**Scenario**: the `apply` subcommand retroactively moves existing IMAP messages
to alias folders.  If rules contain errors (wrong folder name, wrong alias), the
wrong messages are moved.  Because IMAP MOVE is destructive, the operation is
not easily undoable from within autosieve.

**Consequence**: messages may be misplaced or, if the target folder is invalid
and the server discards them, permanently lost.

**Mitigations**:
- **Always run `--dry-run` first** to see which messages would be moved without
  actually moving them.
- Verify the generated alias rules with a test run before using `apply` in production.
- Ensure `use_create = true` (or pre-create folders) so that a missing target
  folder triggers a visible error rather than silently discarding messages.
- Keep IMAP-level backups or snapshots before running `apply` on a large mailbox.
- Limit `apply` to a small source folder initially; confirm the results before
  running on the full mailbox.

