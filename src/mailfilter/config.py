"""Configuration loading and rule dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HEADERS = ["X-Original-To", "Delivered-To"]


class ConfigError(ValueError):
    """Raised for invalid configuration data."""


@dataclass
class Rule:
    aliases: list[str]
    folder: str
    comment: str | None = None


@dataclass
class Config:
    headers: list[str]
    use_create: bool
    script_name: str
    rules: list[Rule]
    explicit_keep: bool
    match_type: str


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_rules(raw_rules: Any) -> list[Rule]:
    rules: list[Rule] = []

    if isinstance(raw_rules, dict):
        for alias, folder in raw_rules.items():
            if not isinstance(alias, str) or not isinstance(folder, str):
                raise ConfigError("rules dict must map string alias -> string folder")
            rules.append(Rule(aliases=[alias.strip()], folder=folder.strip()))
        return rules

    if not isinstance(raw_rules, list):
        raise ConfigError("'rules' must be a list or an object mapping alias->folder")

    for idx, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"rule #{idx} must be an object")

        folder = item.get("folder")
        comment = item.get("comment")
        alias_raw = item.get("alias")
        aliases_raw = item.get("aliases")

        if not isinstance(folder, str) or not folder.strip():
            raise ConfigError(f"rule #{idx} needs a non-empty string 'folder'")

        aliases: list[str] = []
        if isinstance(alias_raw, str):
            aliases.append(alias_raw)
        if aliases_raw is not None:
            if not isinstance(aliases_raw, list) or not all(
                isinstance(x, str) for x in aliases_raw
            ):
                raise ConfigError(f"rule #{idx} field 'aliases' must be a list of strings")
            aliases.extend(aliases_raw)

        aliases = [x.strip() for x in aliases if x.strip()]
        if not aliases:
            raise ConfigError(f"rule #{idx} needs 'alias' or 'aliases'")

        rules.append(
            Rule(
                aliases=aliases,
                folder=folder.strip(),
                comment=comment.strip() if isinstance(comment, str) and comment.strip() else None,
            )
        )

    return rules


def load_config(path: Path) -> Config:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ConfigError("top-level JSON must be an object")

    headers = raw.get("headers", DEFAULT_HEADERS)
    if (
        not isinstance(headers, list)
        or not headers
        or not all(isinstance(h, str) and h.strip() for h in headers)
    ):
        raise ConfigError("'headers' must be a non-empty list of header names")
    headers = [h.strip() for h in headers]

    use_create = bool(raw.get("use_create", False))
    explicit_keep = bool(raw.get("explicit_keep", False))
    match_type = str(raw.get("match_type", "is")).strip().lower()
    if match_type not in {"is", "contains"}:
        raise ConfigError("'match_type' must be 'is' or 'contains'")

    script_name = str(raw.get("script_name", "alias-router")).strip()
    if not script_name:
        raise ConfigError("'script_name' must not be empty")

    raw_rules = raw.get("rules")
    if raw_rules is None:
        raise ConfigError("missing 'rules'")

    return Config(
        headers=headers,
        use_create=use_create,
        script_name=script_name,
        rules=_normalize_rules(raw_rules),
        explicit_keep=explicit_keep,
        match_type=match_type,
    )
