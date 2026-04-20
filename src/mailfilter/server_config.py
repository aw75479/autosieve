"""Load server settings from a TOML config file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImapSettings:
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    connection_security: str = "ssl"
    insecure: bool = False
    folders: list[str] = field(default_factory=lambda: ["INBOX"])
    domain: str = ""
    headers: list[str] = field(default_factory=lambda: ["To", "Delivered-To", "X-Original-To"])
    incremental: bool = True


@dataclass
class ManageSieveSettings:
    host: str = ""
    port: int = 4190
    username: str = ""
    password: str = ""
    connection_security: str = "auto"
    insecure: bool = False
    authz_id: str = ""
    folder_prefix: str = "alias"


@dataclass
class FilenameSettings:
    sieve_file: str = "mailfilter.sieve"
    alias_file: str = "aliases.json"


@dataclass
class ServerConfig:
    imap: ImapSettings = field(default_factory=ImapSettings)
    managesieve: ManageSieveSettings = field(default_factory=ManageSieveSettings)
    filenames: FilenameSettings = field(default_factory=FilenameSettings)


def load_server_config(path: Path) -> ServerConfig:
    """Load server configuration from a TOML file."""
    with path.open("rb") as f:
        raw = tomllib.load(f)

    cfg = ServerConfig()

    if "imap" in raw:
        sec = raw["imap"]
        # Support both "folder" (string) and "folders" (list).
        raw_folders = sec.get("folders", sec.get("folder", ["INBOX"]))
        folders = [raw_folders] if isinstance(raw_folders, str) else list(raw_folders)
        # fmt: off
        cfg.imap = ImapSettings(
            host                = str(sec.get("host", "")),
            port                = int(sec.get("port", 993)),
            user                = str(sec.get("user", "")),
            password            = str(sec.get("password", "")),
            connection_security = str(sec.get("connection_security", "ssl")),
            insecure            = bool(sec.get("insecure", False)),
            folders             = folders,
            domain              = str(sec.get("domain", "")),
            headers             = sec.get("headers", ["To", "Delivered-To", "X-Original-To"]),
            incremental         = bool(sec.get("incremental", True)),
        )
        # fmt: on

    if "managesieve" in raw:
        sec = raw["managesieve"]
        # folder_prefix can live in [managesieve] or legacy [alias] section.
        folder_prefix = str(sec.get("folder_prefix", raw.get("alias", {}).get("folder_prefix", "alias")))
        # fmt: off
        cfg.managesieve = ManageSieveSettings(
            host                = str(sec.get("host", "")),
            port                = int(sec.get("port", 4190)),
            username            = str(sec.get("username", "")),
            password            = str(sec.get("password", "")),
            connection_security = str(sec.get("connection_security", "auto")),
            insecure            = bool(sec.get("insecure", False)),
            authz_id            = str(sec.get("authz_id", "")),
            folder_prefix       = folder_prefix,
        )
        # fmt: on
    elif "alias" in raw:
        # Legacy: read folder_prefix from standalone [alias] section.
        cfg.managesieve.folder_prefix = str(raw["alias"].get("folder_prefix", "alias"))

    # [filenames] section (legacy name: [output]).
    fn_sec = raw.get("filenames", raw.get("output"))
    if fn_sec:
        cfg.filenames = FilenameSettings(
            sieve_file=str(fn_sec.get("sieve_file", "mailfilter.sieve")),
            alias_file=str(fn_sec.get("alias_file", "aliases.json")),
        )

    return cfg
