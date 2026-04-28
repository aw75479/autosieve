"""Load multi-target server settings from a TOML config file.

The v0.2.0 config schema supports multiple independent IMAP/ManageSieve
*targets* (e.g. one per mail account).  Each target carries its own IMAP
and ManageSieve settings, an optional per-target data folder, and optional
feature blocks (vacation, custom_filters, notify, oauth2) which are loaded
lazily by the corresponding feature modules under :mod:`autosieve.features`.

Example ``mailfilter.toml``::

    default_target = "personal"
    data_dir = "./targets"

    [[targets]]
    name = "personal"
    [targets.imap]
    host = "mail.example.com"
    user = "you@example.com"
    [targets.managesieve]
    host = "mail.example.com"
    username = "you@example.com"

    [[targets]]
    name = "work"
    [targets.imap]
    host = "imap.work.example"
    user = "me@work.example"
    auth = "xoauth2"
    [targets.managesieve]
    host = "imap.work.example"
    username = "me@work.example"

A *single-target* config (no ``[[targets]]`` array, but a top-level
``[imap]`` / ``[managesieve]`` / ``[filenames]`` section) is still accepted
and exposed as a single anonymous target named ``"default"``.  This keeps
trivial configs short while allowing the same loader to serve both shapes.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------

DEFAULT_IMAP_PORT: int = 993
DEFAULT_MS_PORT: int = 4190
DEFAULT_SIEVE_FILE: str = "aliasfilter.sieve"
DEFAULT_ALIAS_FILE: str = "aliases.json"
DEFAULT_FOLDER_PREFIX: str = "alias"
DEFAULT_FOLDER_SEP: str = "."
DEFAULT_IMAP_SECURITY: str = "ssl"
DEFAULT_MS_SECURITY: str = "ssl"
DEFAULT_HEADERS: list[str] = ["To", "Delivered-To", "X-Original-To"]
DEFAULT_FOLDERS: list[str] = ["INBOX"]
DEFAULT_DATA_DIR: str = "./targets"
DEFAULT_TARGET_NAME: str = "default"

_VALID_SECURITY = frozenset({"ssl", "starttls", "none"})
_VALID_AUTH = frozenset({"password", "xoauth2"})


class ConfigSchemaError(ValueError):
    """Raised when the TOML config file is structurally invalid."""


# ---------------------------------------------------------------------------
# Per-section settings
# ---------------------------------------------------------------------------


@dataclass
class ImapSettings:
    """IMAP connection and extraction settings for a single target."""

    host: str = ""
    port: int = DEFAULT_IMAP_PORT
    user: str = ""
    password: str = ""
    auth: str = "password"
    connection_security: str = DEFAULT_IMAP_SECURITY
    store_password: bool = False
    folders: list[str] = field(default_factory=lambda: list(DEFAULT_FOLDERS))
    domain: str = ""
    headers: list[str] = field(default_factory=lambda: list(DEFAULT_HEADERS))
    incremental: bool = True
    folder_sep: str = DEFAULT_FOLDER_SEP


@dataclass
class ManageSieveSettings:
    """ManageSieve connection and upload settings for a single target."""

    host: str = ""
    port: int = DEFAULT_MS_PORT
    username: str = ""
    password: str = ""
    auth: str = "password"
    connection_security: str = DEFAULT_MS_SECURITY
    store_password: bool = False
    authz_id: str = ""
    folder_prefix: str = DEFAULT_FOLDER_PREFIX
    folder_sep: str = DEFAULT_FOLDER_SEP
    use_imap_password: bool = False
    scripts: list[str] = field(default_factory=list)
    """Names of ManageSieve scripts this target manages.

    Empty means: a single script derived from the alias file (default).  When
    set, additional script names (e.g. ``"vacation"``, ``"custom"``) are
    expected to be uploaded by the corresponding feature modules.
    """


@dataclass
class FilenameSettings:
    """Default file paths used when not specified on the command line.

    Paths are resolved relative to the *target's* data folder (see
    :meth:`Target.data_dir`).  Use absolute paths to escape that folder.
    """

    sieve_file: str = DEFAULT_SIEVE_FILE
    alias_file: str = DEFAULT_ALIAS_FILE


# Optional feature settings: each is loaded only when the corresponding TOML
# block is present.  Keeping these as plain dicts here avoids hard-coupling
# server_config.py to feature modules; the feature modules parse their own
# blocks via :func:`Target.feature_block`.


@dataclass
class Target:
    """One IMAP/ManageSieve account with optional feature blocks.

    A *target* is the unit of configuration that all CLI commands operate on
    via the ``--target NAME`` flag (or the ``default_target`` config key).

    Attributes:
        name: Stable identifier; used as the per-target data folder name and
            as the ``--target`` CLI argument.
        imap: IMAP settings (always present; defaults if section absent).
        managesieve: ManageSieve settings (always present; defaults if absent).
        filenames: Default filenames for ``aliases.json`` / ``mailfilter.sieve``.
        data_dir_override: Optional explicit per-target data folder.  When
            ``None``, the data folder is computed as
            ``<top.data_dir>/<name>``.  Resolve via :meth:`data_dir`.
        features: Raw TOML blocks for optional features (e.g. ``vacation``,
            ``custom_filters``, ``notify``, ``oauth2``).  Feature modules
            parse their own block from this dict; unknown keys are kept
            verbatim so adding a new feature does not require editing this
            file.
    """

    name: str
    imap: ImapSettings = field(default_factory=ImapSettings)
    managesieve: ManageSieveSettings = field(default_factory=ManageSieveSettings)
    filenames: FilenameSettings = field(default_factory=FilenameSettings)
    data_dir_override: str | None = None
    features: dict[str, Any] = field(default_factory=dict)

    def data_dir(self, base_data_dir: str = DEFAULT_DATA_DIR) -> Path:
        """Return the absolute per-target data folder.

        Args:
            base_data_dir: Top-level ``data_dir`` from the config (default
                ``./targets``).  Ignored when this target carries an explicit
                :attr:`data_dir_override`.
        """
        if self.data_dir_override:
            return Path(self.data_dir_override).expanduser()
        return Path(base_data_dir).expanduser() / self.name

    def alias_path(self, base_data_dir: str = DEFAULT_DATA_DIR) -> Path:
        """Return the absolute path of this target's alias JSON file."""
        p = Path(self.filenames.alias_file)
        return p if p.is_absolute() else self.data_dir(base_data_dir) / p

    def sieve_path(self, base_data_dir: str = DEFAULT_DATA_DIR) -> Path:
        """Return the absolute path of this target's primary sieve script file."""
        p = Path(self.filenames.sieve_file)
        return p if p.is_absolute() else self.data_dir(base_data_dir) / p

    def feature_block(self, name: str) -> Any:
        """Return the raw TOML block for *name*, or ``None`` if not configured."""
        return self.features.get(name)


@dataclass
class ServerConfig:
    """Top-level config: one or more :class:`Target` plus shared settings.

    A *single-target* TOML file (legacy/trivial layout with top-level
    ``[imap]`` / ``[managesieve]`` sections instead of ``[[targets]]``) is
    materialised into one :class:`Target` named ``"default"``.
    """

    targets: list[Target] = field(default_factory=list)
    default_target: str = DEFAULT_TARGET_NAME
    data_dir: str = DEFAULT_DATA_DIR

    # ---- Convenience accessors ----

    def target_names(self) -> list[str]:
        """Return the names of all configured targets, in declaration order."""
        return [t.name for t in self.targets]

    def get_target(self, name: str | None = None) -> Target:
        """Resolve a target by name; falls back to :attr:`default_target`.

        Args:
            name: Target name, or ``None`` to use :attr:`default_target`.

        Raises:
            :class:`ConfigSchemaError`: If no target matches.
        """
        wanted = name or self.default_target
        for t in self.targets:
            if t.name == wanted:
                return t
        if name is None and len(self.targets) == 1:
            # Convenience: single-target configs don't need default_target set.
            return self.targets[0]
        raise ConfigSchemaError(f"unknown target {wanted!r}; available: {self.target_names()!r}")

    # ---- Backward-compatibility shims ----
    #
    # The pre-0.2.0 single-target API exposed ``cfg.imap``, ``cfg.managesieve``,
    # ``cfg.filenames`` directly.  Many call sites still use that shape; the
    # shims forward to the default target so legacy code keeps working until it
    # is migrated to ``cfg.get_target(name).imap`` etc.

    @property
    def imap(self) -> ImapSettings:
        """Forward to the default target's IMAP settings (legacy shim)."""
        return self.get_target().imap

    @property
    def managesieve(self) -> ManageSieveSettings:
        """Forward to the default target's ManageSieve settings (legacy shim)."""
        return self.get_target().managesieve

    @property
    def filenames(self) -> FilenameSettings:
        """Forward to the default target's filename settings (legacy shim)."""
        return self.get_target().filenames


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_security(value: str, section: str) -> str:
    v = value.strip().lower()
    if v not in _VALID_SECURITY:
        raise ConfigSchemaError(f"[{section}] connection_security must be one of {sorted(_VALID_SECURITY)!r}; got {value!r}")
    return v


def _parse_auth(value: str, section: str) -> str:
    v = value.strip().lower()
    if v not in _VALID_AUTH:
        raise ConfigSchemaError(f"[{section}] auth must be one of {sorted(_VALID_AUTH)!r}; got {value!r}")
    return v


def _imap_from_dict(sec: dict[str, Any]) -> ImapSettings:
    raw_folders = sec.get("folders", DEFAULT_FOLDERS)
    folders = [raw_folders] if isinstance(raw_folders, str) else list(raw_folders)
    return ImapSettings(
        host=str(sec.get("host", "")),
        port=int(sec.get("port", DEFAULT_IMAP_PORT)),
        user=str(sec.get("user", "")),
        password=str(sec.get("password", "")),
        auth=_parse_auth(str(sec.get("auth", "password")), "imap"),
        connection_security=_parse_security(str(sec.get("connection_security", DEFAULT_IMAP_SECURITY)), "imap"),
        store_password=bool(sec.get("store_password", False)),
        folders=folders,
        domain=str(sec.get("domain", "")),
        headers=list(sec.get("headers", DEFAULT_HEADERS)),
        incremental=bool(sec.get("incremental", True)),
        folder_sep=str(sec.get("folder_sep", DEFAULT_FOLDER_SEP)),
    )


def _ms_from_dict(sec: dict[str, Any]) -> ManageSieveSettings:
    return ManageSieveSettings(
        host=str(sec.get("host", "")),
        port=int(sec.get("port", DEFAULT_MS_PORT)),
        username=str(sec.get("username", "")),
        password=str(sec.get("password", "")),
        auth=_parse_auth(str(sec.get("auth", "password")), "managesieve"),
        connection_security=_parse_security(str(sec.get("connection_security", DEFAULT_MS_SECURITY)), "managesieve"),
        store_password=bool(sec.get("store_password", False)),
        authz_id=str(sec.get("authz_id", "")),
        folder_prefix=str(sec.get("folder_prefix", DEFAULT_FOLDER_PREFIX)),
        folder_sep=str(sec.get("folder_sep", DEFAULT_FOLDER_SEP)),
        use_imap_password=bool(sec.get("use_imap_password", False)),
        scripts=list(sec.get("scripts", [])),
    )


def _filenames_from_dict(sec: dict[str, Any]) -> FilenameSettings:
    return FilenameSettings(
        sieve_file=str(sec.get("sieve_file", DEFAULT_SIEVE_FILE)),
        alias_file=str(sec.get("alias_file", DEFAULT_ALIAS_FILE)),
    )


def _target_from_dict(raw: dict[str, Any], default_name: str) -> Target:
    """Parse a single ``[[targets]]`` dict into a :class:`Target`.

    Recognised sub-tables: ``imap``, ``managesieve``, ``filenames``.  Any
    other sub-table (e.g. ``vacation``, ``custom_filters``, ``notify``,
    ``oauth2``) is preserved verbatim under :attr:`Target.features` for the
    corresponding feature module to consume.
    """
    name = str(raw.get("name", default_name)).strip() or default_name
    imap = _imap_from_dict(raw.get("imap", {})) if "imap" in raw else ImapSettings()
    ms = _ms_from_dict(raw.get("managesieve", {})) if "managesieve" in raw else ManageSieveSettings()
    fn = _filenames_from_dict(raw.get("filenames", {})) if "filenames" in raw else FilenameSettings()
    data_dir_override = raw.get("data_dir")
    known = {"name", "imap", "managesieve", "filenames", "data_dir"}
    features = {k: v for k, v in raw.items() if k not in known}
    return Target(
        name=name,
        imap=imap,
        managesieve=ms,
        filenames=fn,
        data_dir_override=str(data_dir_override) if data_dir_override else None,
        features=features,
    )


def load_server_config(path: Path) -> ServerConfig:
    """Load server configuration from a TOML file.

    Accepts either of two shapes:

    1. **Multi-target** (preferred since v0.2.0)::

           default_target = "personal"
           data_dir = "./targets"
           [[targets]]
           name = "personal"
           [targets.imap]
           ...

    2. **Single-target** (trivial layout, materialised as one target named
       ``"default"``)::

           [imap]
           ...
           [managesieve]
           ...

    Args:
        path: Path to the TOML configuration file.

    Returns:
        A populated :class:`ServerConfig` with at least one :class:`Target`.

    Raises:
        :class:`ConfigSchemaError`: Invalid value or contradictory shape
            (e.g. both top-level ``[imap]`` and a ``[[targets]]`` array).
        :class:`tomllib.TOMLDecodeError`: If the file is not valid TOML.
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)

    data_dir = str(raw.get("data_dir", DEFAULT_DATA_DIR))
    default_target = str(raw.get("default_target", DEFAULT_TARGET_NAME))

    raw_targets = raw.get("targets")
    has_top_level_target = any(k in raw for k in ("imap", "managesieve", "filenames"))

    if raw_targets is not None and has_top_level_target:
        raise ConfigSchemaError("config has both [[targets]] and a top-level [imap]/[managesieve]/[filenames] section; choose one shape")

    targets: list[Target] = []
    if raw_targets is not None:
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ConfigSchemaError("'targets' must be a non-empty array of tables ([[targets]])")
        seen_names: set[str] = set()
        for idx, t_raw in enumerate(raw_targets, start=1):
            if not isinstance(t_raw, dict):
                raise ConfigSchemaError(f"targets[{idx}] must be a table")
            t = _target_from_dict(t_raw, default_name=f"target{idx}")
            if t.name in seen_names:
                raise ConfigSchemaError(f"duplicate target name {t.name!r}")
            seen_names.add(t.name)
            targets.append(t)
    else:
        # Single-target layout: synthesize one target named "default".
        flat = {
            "name": DEFAULT_TARGET_NAME,
            "imap": raw.get("imap", {}),
            "managesieve": raw.get("managesieve", {}),
            "filenames": raw.get("filenames", {}),
        }
        targets.append(_target_from_dict(flat, default_name=DEFAULT_TARGET_NAME))

    # When default_target is left at its sentinel and there's exactly one
    # target with a different name, treat that one as the implicit default.
    if default_target == DEFAULT_TARGET_NAME and len(targets) == 1 and targets[0].name != DEFAULT_TARGET_NAME:
        default_target = targets[0].name

    return ServerConfig(targets=targets, default_target=default_target, data_dir=data_dir)
