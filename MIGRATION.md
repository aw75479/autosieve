# Migrating from 0.1.x to 0.2.0

Version 0.2.0 introduces the multi-target schema, the `autosieve` package
name, and a feature plugin layout. This guide walks through the
mechanical changes.

## 1. Rename the package and console script

Old:

```bash
pip install mailfilter
mailfilter generate aliases.json
```

New:

```bash
pip install autosieve
autosieve generate aliases.json
```

The console script `mailfilter` has been removed. The Python package is
now `autosieve`; any imports must change accordingly:

```python
# old
from mailfilter.config import load_alias_config

# new
from autosieve.config import load_alias_config
```

## 2. Rename the system keyring service

Stored passwords now live under the keyring service `autosieve` (was
`mailfilter`). To migrate an entry:

```bash
keyring get mailfilter "imap://you@example.com" \
    | xargs -I{} keyring set autosieve "imap://you@example.com" {}
keyring del mailfilter "imap://you@example.com"
```

(Or just delete the old entry and let the next CLI run re-prompt and
store under the new name with `--store-password`.)

## 3. Rename the config file

Rename `mailfilter.toml` to `autosieve.toml` (autosieve auto-loads either
name from the current directory, but the new name is preferred):

```bash
mv mailfilter.toml autosieve.toml
```

## 4. Convert flat config to multi-target format

The 0.1.x config used flat top-level sections:

```toml
[imap]
host = "mail.example.com"
user = "you@example.com"

[managesieve]
host = "mail.example.com"
username = "you@example.com"

[filenames]
sieve_file = "aliasfilter.sieve"
alias_file = "aliases.json"
```

The 0.2 loader still accepts this flat shape and materialises it as a
single target named `default`, so **existing configs keep working
unchanged**. To opt in to the new multi-target features, wrap the
sections in a `[[targets]]` block:

```toml
data_dir = "./targets"

[[targets]]
name = "personal"

[targets.imap]
host = "mail.example.com"
user = "you@example.com"

[targets.managesieve]
host = "mail.example.com"
username = "you@example.com"

[targets.filenames]
# Optional now -- defaults to <data_dir>/<name>/aliasfilter.sieve and
# <data_dir>/<name>/aliases.json.
sieve_file = "./targets/personal/aliasfilter.sieve"
alias_file = "./targets/personal/aliases.json"
```

Add additional `[[targets]]` blocks for other accounts and select them
with `--target NAME` on any subcommand.

## 5. Move per-target data files

By default, a target named `personal` now stores its files under
`./targets/personal/`. Move (or symlink) your existing files:

```bash
mkdir -p targets/personal
mv aliases.json        targets/personal/aliases.json
mv aliasfilter.sieve   targets/personal/aliasfilter.sieve
```

Or keep the explicit paths under `[targets.filenames]` -- they take
precedence over the per-target default.

## 6. Add `targets/` to `.gitignore`

The shipped `.gitignore` now excludes `targets/`, `*.bak`, `backups/`,
`.tokens/`, and `*.oauthcache`. Make sure your local `.gitignore`
contains these entries so that PII (alias names, server secrets) does
not get committed.

## 7. Optional: enable new features

Each feature is a separate file under `src/autosieve/features/`; delete
the file to disable it. To enable a feature, add the corresponding
config block under your target. See `autosieve.template.toml` for
copy-pasteable examples of:

* `[targets.features.vacation]` - RFC 5230 auto-reply
* `[[targets.features.notify.rules]]` - RFC 5435 enotify
* `[[targets.features.custom_filters.rules]]` - extra Sieve rules
* `[targets.features.oauth2]` - XOAUTH2 token resolution

## 8. New commands at a glance

* `autosieve sync` -- run the full extract/generate/apply/upload pipeline
  with sensible confirmation prompts.
* `autosieve backup [--remote]` -- snapshot the target's local files
  (and optionally all server-side scripts) under
  `<data_dir>/<target>/backups/<ISO timestamp>/`.
* `autosieve restore [--remote]` -- restore from a snapshot.
* `--tag NAME` on `generate` and `apply` to operate only on rules
  carrying that tag.
