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
    headers: list[str] | None = None
    active: bool = True


@dataclass
class Config:
    headers: list[str]
    use_create: bool
    script_name: str
    rules: list[Rule]
    explicit_keep: bool
    match_type: str
    generation_mode: str = "header"
    catch_all_folder: str | None = None
    folder_prefix: str = "alias"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _merge_rules_by_folder(rules: list[Rule]) -> list[Rule]:
    """Merge rules that target the same folder into a single rule.

    The first rule's comment wins when merging. Alias order is preserved,
    duplicates are removed.
    """
    folder_order: list[str] = []
    by_folder: dict[str, Rule] = {}
    for rule in rules:
        if rule.folder in by_folder:
            existing = by_folder[rule.folder]
            seen = set(existing.aliases)
            for alias in rule.aliases:
                if alias not in seen:
                    existing.aliases.append(alias)
                    seen.add(alias)
            if existing.comment is None and rule.comment is not None:
                existing.comment = rule.comment
            # If any merged rule is inactive, the merged result is inactive.
            if not rule.active:
                existing.active = False
            # Merge per-rule headers.
            if existing.headers is not None and rule.headers is not None:
                seen_h = set(existing.headers)
                for h in rule.headers:
                    if h not in seen_h:
                        existing.headers.append(h)
                        seen_h.add(h)
            elif rule.headers is not None:
                existing.headers = list(rule.headers)
        else:
            folder_order.append(rule.folder)
            by_folder[rule.folder] = Rule(
                aliases=list(rule.aliases),
                folder=rule.folder,
                comment=rule.comment,
                headers=list(rule.headers) if rule.headers else None,
                active=rule.active,
            )
    return [by_folder[f] for f in folder_order]


def _normalize_rules(raw_rules: Any) -> list[Rule]:
    rules: list[Rule] = []

    if isinstance(raw_rules, dict):
        for alias, folder in raw_rules.items():
            if not isinstance(alias, str) or not isinstance(folder, str):
                raise ConfigError("rules dict must map string alias -> string folder")
            rules.append(Rule(aliases=[alias.strip()], folder=folder.strip()))
        return _merge_rules_by_folder(rules)

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
            if not isinstance(aliases_raw, list) or not all(isinstance(x, str) for x in aliases_raw):
                raise ConfigError(f"rule #{idx} field 'aliases' must be a list of strings")
            aliases.extend(aliases_raw)

        aliases = [x.strip() for x in aliases if x.strip()]
        if not aliases:
            raise ConfigError(f"rule #{idx} needs 'alias' or 'aliases'")

        # Per-rule headers override.
        headers_raw = item.get("headers")
        rule_headers: list[str] | None = None
        if isinstance(headers_raw, list) and all(isinstance(h, str) for h in headers_raw):
            rule_headers = [h.strip() for h in headers_raw if h.strip()] or None

        active = item.get("active", True)
        if not isinstance(active, bool):
            active = True

        rules.append(
            Rule(
                aliases=aliases,
                folder=folder.strip(),
                comment=comment.strip() if isinstance(comment, str) and comment.strip() else None,
                headers=rule_headers,
                active=active,
            )
        )

    return _merge_rules_by_folder(rules)


def load_config(path: Path) -> Config:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ConfigError("top-level JSON must be an object")

    headers = raw.get("headers", DEFAULT_HEADERS)
    if not isinstance(headers, list) or not headers or not all(isinstance(h, str) and h.strip() for h in headers):
        raise ConfigError("'headers' must be a non-empty list of header names")
    headers = [h.strip() for h in headers]

    use_create = bool(raw.get("use_create", False))
    explicit_keep = bool(raw.get("explicit_keep", False))
    match_type = str(raw.get("match_type", "is")).strip().lower()
    if match_type not in {"is", "contains", "matches", "regex"}:
        raise ConfigError("'match_type' must be 'is', 'contains', 'matches', or 'regex'")

    generation_mode = str(raw.get("generation_mode", "header")).strip().lower()
    if generation_mode not in {"header", "envelope"}:
        raise ConfigError("'generation_mode' must be 'header' or 'envelope'")

    catch_all_folder_raw = raw.get("catch_all_folder")
    catch_all_folder: str | None = None
    if isinstance(catch_all_folder_raw, str) and catch_all_folder_raw.strip():
        catch_all_folder = catch_all_folder_raw.strip()

    folder_prefix = str(raw.get("folder_prefix", "alias")).strip()

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
        generation_mode=generation_mode,
        catch_all_folder=catch_all_folder,
        folder_prefix=folder_prefix,
    )
