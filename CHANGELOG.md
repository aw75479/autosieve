# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - unreleased

### Added

* **Multi-target configuration**. The TOML config now uses an array of
  `[[targets]]` blocks. Each target carries its own IMAP, ManageSieve,
  filenames, and feature settings, and gets its own data directory under
  `<data_dir>/<target.name>/` (default `./targets/<name>/`). Pass
  `--target NAME` on any subcommand to choose a target; otherwise the
  config's `default_target` (or the first listed target) is used.
* **`sync` command**: chains `extract -> generate -> apply -> upload` for
  one target. Confirmation policy: `--yes` skips prompts for the
  non-destructive steps; the destructive `apply` step requires `--yes-apply`
  (or interactive `y/N`) since it moves real mail. Per-step skip flags:
  `--no-extract`, `--no-apply`, `--no-upload`.
* **`backup` / `restore` commands**: snapshot a target's `aliases.json`
  and local sieve file (and, with `--remote`, all server-side scripts via
  ManageSieve `LISTSCRIPTS` + `GETSCRIPT`). Restore is interactive;
  `--remote` upload requires the extra `--yes-remote` flag.
* **`autosieve.features` package**: every feature is one removable file.
  * `vacation` (RFC 5230) auto-reply with subject / body / `body_file` /
    days / addresses / handle.
  * `notify` (RFC 5435) with rules matching `if_from`, `if_to`,
    `if_subject` and emitting `notify :method ... :message ...`.
  * `custom_filters` for non-alias rules (header/from/to/cc/subject/body
    matchers; actions `fileinto`, `discard`, `redirect`, `keep`, `stop`;
    glob patterns auto-promote to `:matches`).
  * `oauth2` XOAUTH2 token resolution: `token_command` strategy ships
    fully working; `provider = "gmail" | "microsoft"` is scaffolded with a
    clear error until a user supplies their own client id (see
    [docs/security.md](docs/security.md)).
* **Alias tags**: `Rule` now carries `tags: list[str]`. `generate` and
  `apply` accept `--tag NAME` to operate only on rules matching that tag.
* `ManageSieveClient.get_script()` and `delete_script()` (used by
  backup/restore).
* `[project.optional-dependencies] oauth2 = ["requests>=2.31"]` for
  future built-in OAuth2 device-code flow.

### Changed

* **Renamed `mailfilter` -> `autosieve`** (package, console script,
  template config, system-keyring service id). The `mailfilter` shim
  console script has been removed.
* Default config filename: `autosieve.toml` (legacy `mailfilter.toml` is
  still auto-loaded for backwards compatibility).
* `write_output()` and `write_alias_mapping()` now create parent
  directories on demand so per-target data folders work on first run.

### Removed

* The legacy `mailfilter` console script alias.
* The committed `alias.json.old` privacy leak (file is gone from the
  current tree; rewrite history with `git filter-repo` if you need it
  removed from older commits).

### Migration

See [MIGRATION.md](MIGRATION.md) for step-by-step upgrade instructions
from 0.1.x.
