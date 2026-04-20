# Advanced Features

Possible future enhancements for mailfilter, roughly ordered by expected value.

## High priority

- ~~**Regex / wildcard matching**: support `match_type: "regex"` or `"matches"`
  for Sieve `:regex` and `:matches` comparators.  Useful for catch-all
  patterns like `*@subdomain.example.com`.~~ **Implemented.**

- ~~**Dry-run / diff mode**: show what would change in the alias file or Sieve
  script without writing anything.  Compare the current server-side script
  with the newly generated one.~~ **Implemented** (`--dry-run` flag).

- ~~**Multi-folder scanning**: scan multiple IMAP folders (e.g. INBOX plus
  Sent) to discover aliases from both incoming and outgoing mail.~~
  **Implemented** (`--folder INBOX Sent` or `folders = ["INBOX", "Sent"]`).

- ~~**Alias deactivation**: mark aliases as inactive without deleting them.
  Inactive aliases are preserved in the JSON but excluded from the generated
  Sieve script.~~ **Implemented** (`"active": false` in rule JSON).

## Medium priority

- **Sieve `vacation` / auto-reply rules**: generate vacation responders for
  specific aliases.

- **Priority / ordering control**: allow explicit rule ordering or priority
  levels so that more specific rules are checked before broader ones.

- **Multiple script support**: manage several Sieve scripts (e.g. one for
  alias routing, one for spam filtering) and activate them in order.

- **IMAP OAuth2 authentication**: support OAuth2 (XOAUTH2 / OAUTHBEARER)
  for providers that require it (e.g. Gmail, Microsoft 365).

- **Envelope-based matching**: use Sieve `envelope` test instead of header
  matching for more precise alias detection.

- **Alias grouping / tagging**: group aliases by tag (e.g. "clients",
  "newsletters") and apply bulk operations per group.

## Lower priority

- **Web UI**: a lightweight web interface for reviewing and editing the alias
  file, with a live Sieve preview.

- **Notification on new aliases**: send a notification (email, webhook) when
  new aliases are discovered during incremental extraction.

- **Backup / restore**: download the current server-side Sieve script as a
  backup before uploading a new one.

- **Multi-server support**: manage aliases and Sieve scripts across multiple
  mail servers from a single configuration.

- **Sieve `notify` actions**: generate notification rules (e.g. push
  notification for high-priority aliases).

- **Statistics / reporting**: report on alias usage frequency based on IMAP
  scan data (how many messages per alias).

- **Thunderbird filter export**: export rules as Thunderbird-compatible
  `msgFilterRules.dat` for local filtering in addition to server-side Sieve.

- **CalDAV / CardDAV integration**: cross-reference discovered aliases with
  contacts to auto-assign folder names based on contact display names.
