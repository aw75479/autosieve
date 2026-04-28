"""RFC 5435 ``enotify`` (notify action) feature.

Independent module: delete this file to disable the feature.

Configuration (TOML, per target)::

    [targets.features.notify]
    enabled = true

    [[targets.features.notify.rules]]
    name    = "urgent-from-boss"
    if_from = "boss@example.com"
    method  = "mailto:phone@sms.example.com"
    message = "URGENT: ${subject}"

    [[targets.features.notify.rules]]
    name        = "subject-keyword"
    if_subject  = "URGENT"
    method      = "xmpp:me@xmpp.example.com"

Each rule generates a Sieve ``if`` block ending with a ``notify`` action.
"""

from __future__ import annotations

from typing import Any


def emit_sieve(target: Any, _alias_config: Any) -> tuple[str, set[str]] | None:
    """Return the notify block + required capabilities, or None if disabled."""
    cfg = target.feature_block("notify")
    if not cfg or not cfg.get("enabled", False):
        return None
    rules = cfg.get("rules") or []
    if not rules:
        return None

    blocks: list[str] = ["# Notify rules (RFC 5435)"]
    for rule in rules:
        name = rule.get("name", "notify")
        method = rule.get("method")
        if not method:
            continue
        message = rule.get("message", "New mail")

        tests: list[str] = []
        if rule.get("if_from"):
            tests.append(f'address :is "From" "{_escape(str(rule["if_from"]))}"')
        if rule.get("if_to"):
            tests.append(f'address :is "To" "{_escape(str(rule["if_to"]))}"')
        if rule.get("if_subject"):
            tests.append(f'header :contains "Subject" "{_escape(str(rule["if_subject"]))}"')

        if not tests:
            continue
        condition = tests[0] if len(tests) == 1 else "allof(" + ", ".join(tests) + ")"
        block = f'# notify: {name}\nif {condition} {{\n    notify :method "{_escape(method)}" :message "{_escape(message)}";\n}}'
        blocks.append(block)

    if len(blocks) == 1:  # only the header comment
        return None
    return "\n\n".join(blocks), {"enotify"}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
