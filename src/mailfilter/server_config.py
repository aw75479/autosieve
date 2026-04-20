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
    folder: str = "INBOX"
    domain: str = ""
    headers: list[str] = field(default_factory=lambda: ["To", "Delivered-To", "X-Original-To"])


@dataclass
class ManageSieveSettings:
    host: str = ""
    port: int = 4190
    username: str = ""
    password: str = ""
    connection_security: str = "auto"
    insecure: bool = False
    authz_id: str = ""


@dataclass
class AliasSettings:
    folder_prefix: str = "alias"


@dataclass
class OutputSettings:
    sieve_file: str = "mailfilter.sieve"
    alias_file: str = "aliases.json"


@dataclass
class ServerConfig:
    imap: ImapSettings = field(default_factory=ImapSettings)
    managesieve: ManageSieveSettings = field(default_factory=ManageSieveSettings)
    alias: AliasSettings = field(default_factory=AliasSettings)
    output: OutputSettings = field(default_factory=OutputSettings)


def load_server_config(path: Path) -> ServerConfig:
    """Load server configuration from a TOML file."""
    with path.open("rb") as f:
        raw = tomllib.load(f)

    cfg = ServerConfig()

    if "imap" in raw:
        sec = raw["imap"]
        cfg.imap = ImapSettings(
            host=str(sec.get("host", "")),
            port=int(sec.get("port", 993)),
            user=str(sec.get("user", "")),
            password=str(sec.get("password", "")),
            connection_security=str(sec.get("connection_security", "ssl")),
            insecure=bool(sec.get("insecure", False)),
            folder=str(sec.get("folder", "INBOX")),
            domain=str(sec.get("domain", "")),
            headers=sec.get("headers", ["To", "Delivered-To", "X-Original-To"]),
        )

    if "managesieve" in raw:
        sec = raw["managesieve"]
        cfg.managesieve = ManageSieveSettings(
            host=str(sec.get("host", "")),
            port=int(sec.get("port", 4190)),
            username=str(sec.get("username", "")),
            password=str(sec.get("password", "")),
            connection_security=str(sec.get("connection_security", "auto")),
            insecure=bool(sec.get("insecure", False)),
            authz_id=str(sec.get("authz_id", "")),
        )

    if "alias" in raw:
        sec = raw["alias"]
        cfg.alias = AliasSettings(
            folder_prefix=str(sec.get("folder_prefix", "alias")),
        )

    if "output" in raw:
        sec = raw["output"]
        cfg.output = OutputSettings(
            sieve_file=str(sec.get("sieve_file", "mailfilter.sieve")),
            alias_file=str(sec.get("alias_file", "aliases.json")),
        )

    return cfg
