"""Custom-filter feature: TOML-driven Sieve rules beyond alias-to-folder.

Independent module: delete this file to disable.

Configuration (TOML, per target)::

    [targets.features.custom_filters]
    enabled = true

    [[targets.features.custom_filters.rules]]
    name        = "newsletters"
    if_from     = "*@newsletters.example.com"
    action      = "fileinto"
    folder      = "INBOX/Newsletters"

    [[targets.features.custom_filters.rules]]
    name        = "spam-subject"
    if_subject  = "WIN A PRIZE"
    action      = "discard"

    [[targets.features.custom_filters.rules]]
    name        = "header-list-id"
    if_header   = ["List-Id", "*<list.example.com>*"]
    action      = "fileinto"
    folder      = "INBOX/Lists"
    stop        = true

Supported test fields: ``if_from``, ``if_to``, ``if_cc``, ``if_subject``,
``if_header = [name, pattern]``, ``if_body``.  Patterns containing ``*``
or ``?`` use Sieve ``:matches``; otherwise ``:contains``.

Supported actions: ``fileinto``, ``discard``, ``redirect``, ``keep``,
``stop``.  Add ``stop = true`` to a rule to insert a ``stop`` after the
action.
"""

from __future__ import annotations

from typing import Any


def emit_sieve(target: Any, _alias_config: Any) -> tuple[str, set[str]] | None:
    """Return the custom-filters block + required capabilities, or None if disabled."""
    cfg = target.feature_block("custom_filters")
    if not cfg or not cfg.get("enabled", False):
        return None
    rules = cfg.get("rules") or []
    if not rules:
        return None

    caps: set[str] = set()
    blocks: list[str] = ["# Custom filters"]
    for rule in rules:
        block, rule_caps = _emit_rule(rule)
        if block:
            blocks.append(block)
            caps.update(rule_caps)
    if len(blocks) == 1:
        return None
    return "\n\n".join(blocks), caps


def _emit_rule(rule: dict[str, Any]) -> tuple[str, set[str]]:
    name = rule.get("name", "custom")
    caps: set[str] = set()
    tests: list[str] = []

    for field, sieve_field in (("if_from", "From"), ("if_to", "To"), ("if_cc", "Cc")):
        if rule.get(field):
            tests.append(_address_test(sieve_field, str(rule[field])))
    if rule.get("if_subject"):
        tests.append(_header_test("Subject", str(rule["if_subject"])))
    if rule.get("if_header"):
        hdr = rule["if_header"]
        if isinstance(hdr, list) and len(hdr) == 2:
            tests.append(_header_test(str(hdr[0]), str(hdr[1])))
    if rule.get("if_body"):
        tests.append(_body_test(str(rule["if_body"])))
        caps.add("body")

    if not tests:
        return "", caps
    condition = tests[0] if len(tests) == 1 else "allof(" + ", ".join(tests) + ")"

    action = rule.get("action", "keep")
    folder = rule.get("folder")
    addr = rule.get("address")
    body_lines: list[str] = []
    if action == "fileinto":
        if not folder:
            return "", caps
        body_lines.append(f'fileinto "{_escape(str(folder))}";')
        caps.add("fileinto")
    elif action == "discard":
        body_lines.append("discard;")
    elif action == "redirect":
        if not addr:
            return "", caps
        body_lines.append(f'redirect "{_escape(str(addr))}";')
    elif action == "keep":
        body_lines.append("keep;")
    elif action == "stop":
        body_lines.append("stop;")
    else:
        return "", caps

    if rule.get("stop"):
        body_lines.append("stop;")

    indented = "\n".join(f"    {line}" for line in body_lines)
    return f"# custom: {name}\nif {condition} {{\n{indented}\n}}", caps


def _is_glob(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern


def _address_test(field: str, pattern: str) -> str:
    op = ":matches" if _is_glob(pattern) else ":contains"
    return f'address {op} "{field}" "{_escape(pattern)}"'


def _header_test(field: str, pattern: str) -> str:
    op = ":matches" if _is_glob(pattern) else ":contains"
    return f'header {op} "{field}" "{_escape(pattern)}"'


def _body_test(pattern: str) -> str:
    op = ":matches" if _is_glob(pattern) else ":contains"
    return f'body :text {op} "{_escape(pattern)}"'


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
