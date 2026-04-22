"""Load server settings from a TOML config file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

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

_VALID_SECURITY = frozenset({"ssl", "starttls", "none"})


@dataclass
class ImapSettings:
    """IMAP connection and extraction settings."""

    host: str = ""
    port: int = DEFAULT_IMAP_PORT
    user: str = ""
    password: str = ""
    connection_security: str = DEFAULT_IMAP_SECURITY
    store_password: bool = False
    folders: list[str] = field(default_factory=lambda: list(DEFAULT_FOLDERS))
    domain: str = ""
    headers: list[str] = field(default_factory=lambda: list(DEFAULT_HEADERS))
    incremental: bool = True
    folder_sep: str = DEFAULT_FOLDER_SEP


@dataclass
class ManageSieveSettings:
    """ManageSieve connection and upload settings."""

    host: str = ""
    port: int = DEFAULT_MS_PORT
    username: str = ""
    password: str = ""
    connection_security: str = DEFAULT_MS_SECURITY
    store_password: bool = False
    authz_id: str = ""
    folder_prefix: str = DEFAULT_FOLDER_PREFIX
    folder_sep: str = DEFAULT_FOLDER_SEP
    use_imap_password: bool = False


@dataclass
class FilenameSettings:
    """Default file paths used when not specified on the command line."""

    sieve_file: str = DEFAULT_SIEVE_FILE
    alias_file: str = DEFAULT_ALIAS_FILE


@dataclass
class ServerConfig:
    """Aggregated server configuration loaded from a TOML file."""

    imap: ImapSettings = field(default_factory=ImapSettings)
    managesieve: ManageSieveSettings = field(default_factory=ManageSieveSettings)
    filenames: FilenameSettings = field(default_factory=FilenameSettings)


def _parse_security(value: str, section: str) -> str:
    """Validate and return a connection security string.

    Args:
        value: The raw string from the config file.
        section: TOML section name used in error messages.

    Returns:
        Lowercased, validated security string.

    Raises:
        ValueError: If *value* is not one of ``ssl``, ``starttls``, or ``none``.
    """
    v = value.strip().lower()
    if v not in _VALID_SECURITY:
        raise ValueError(f"[{section}] connection_security must be one of {sorted(_VALID_SECURITY)!r}; got {value!r}")
    return v


def load_server_config(path: Path) -> ServerConfig:
    """Load server configuration from a TOML file.

    Args:
        path: Path to the TOML configuration file.

    Returns:
        A :class:`ServerConfig` populated from the file; any missing section
        or key falls back to the corresponding dataclass default.

    Raises:
        ValueError: If a ``connection_security`` field contains an invalid value.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)

    cfg = ServerConfig()

    if "imap" in raw:
        sec = raw["imap"]
        raw_folders = sec.get("folders", DEFAULT_FOLDERS)
        folders = [raw_folders] if isinstance(raw_folders, str) else list(raw_folders)
        # fmt: off
        cfg.imap = ImapSettings(
            host                = str(sec.get("host", "")),
            port                = int(sec.get("port", DEFAULT_IMAP_PORT)),
            user                = str(sec.get("user", "")),
            password            = str(sec.get("password", "")),
            connection_security = _parse_security(str(sec.get("connection_security", DEFAULT_IMAP_SECURITY)), "imap"),
            store_password      = bool(sec.get("store_password", False)),
            folders             = folders,
            domain              = str(sec.get("domain", "")),
            headers             = sec.get("headers", list(DEFAULT_HEADERS)),
            incremental         = bool(sec.get("incremental", True)),
            folder_sep          = str(sec.get("folder_sep", DEFAULT_FOLDER_SEP)),
        )
        # fmt: on

    if "managesieve" in raw:
        sec = raw["managesieve"]
        # fmt: off
        cfg.managesieve = ManageSieveSettings(
            host                = str(sec.get("host", "")),
            port                = int(sec.get("port", DEFAULT_MS_PORT)),
            username            = str(sec.get("username", "")),
            password            = str(sec.get("password", "")),
            connection_security = _parse_security(str(sec.get("connection_security", DEFAULT_MS_SECURITY)), "managesieve"),
            store_password      = bool(sec.get("store_password", False)),
            authz_id            = str(sec.get("authz_id", "")),
            folder_prefix       = str(sec.get("folder_prefix", DEFAULT_FOLDER_PREFIX)),
            folder_sep          = str(sec.get("folder_sep", DEFAULT_FOLDER_SEP)),
            use_imap_password   = bool(sec.get("use_imap_password", False)),
        )
        # fmt: on

    fn_sec = raw.get("filenames")
    if fn_sec:
        cfg.filenames = FilenameSettings(
            sieve_file=str(fn_sec.get("sieve_file", DEFAULT_SIEVE_FILE)),
            alias_file=str(fn_sec.get("alias_file", DEFAULT_ALIAS_FILE)),
        )

    return cfg
