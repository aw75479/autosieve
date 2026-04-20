"""Extract email aliases from an IMAP inbox by scanning message headers."""

from __future__ import annotations

import email.utils
import imaplib
import json
import re
import ssl
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import Any

_HEADER_PARSER = BytesHeaderParser()

# Headers to scan for aliases (in addition to Received).
ALIAS_HEADERS = ("To", "Delivered-To", "X-Original-To")

# Pattern matching "for <user@domain>" in Received headers.
_RECEIVED_FOR_RE = re.compile(r"\bfor\s+<([^>]+)>", re.IGNORECASE)


def connect_imap(
    host: str,
    port: int = 993,
    user: str = "",
    password: str = "",
    connection_security: str = "ssl",
    insecure: bool = False,
) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    """Connect and authenticate to an IMAP server."""
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL
    if connection_security == "ssl":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    elif connection_security == "starttls":
        conn = imaplib.IMAP4(host, port)
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn.starttls(ctx)
    else:
        conn = imaplib.IMAP4(host, port)

    conn.login(user, password)
    return conn


def parse_received_for(header_value: str) -> list[str]:
    """Extract email addresses from 'for <addr>' clauses in a Received header."""
    return [m.lower() for m in _RECEIVED_FOR_RE.findall(header_value)]


def _extract_addresses(value: str) -> list[str]:
    """Parse an address header value and return lowercase email addresses."""
    addresses: list[str] = []
    for _display, addr in email.utils.getaddresses([value]):
        addr = addr.strip().lower()
        if "@" in addr:
            addresses.append(addr)
    return addresses


def extract_aliases(
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    folder: str = "INBOX",
    domain: str | None = None,
    headers: tuple[str, ...] = ALIAS_HEADERS,
    limit: int | None = None,
    since: date | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, set[str]]:
    """Scan messages in *folder* and return discovered aliases with their headers.

    Returns a dict mapping each alias address to the set of header names it was
    found in.  Aliases discovered only via ``Received: ... for <addr>`` get an
    empty header set (they should use global headers in sieve rules).

    If *domain* is given, only addresses ending in ``@domain`` are returned.
    If *since* is given, only messages received on or after that date are scanned.
    If *limit* is given, at most *limit* messages are scanned (most recent first).
    *progress* is called with ``(processed, total)`` after each batch.
    """
    conn.select(folder, readonly=True)

    # Build IMAP SEARCH criteria.
    if since is not None:
        date_str = since.strftime("%d-%b-%Y")
        _typ, data = conn.search(None, f"SINCE {date_str}")
    else:
        _typ, data = conn.search(None, "ALL")

    msg_ids = data[0].split() if data[0] else []
    if not msg_ids:
        return {}

    # Most recent first when applying limit.
    msg_ids = list(reversed(msg_ids))
    if limit is not None:
        msg_ids = msg_ids[:limit]

    # Fetch only the headers we need, in batches to avoid IMAP argument length limits.
    fetch_headers = [*headers, "Received"]
    header_list = " ".join(fetch_headers)

    aliases: dict[str, set[str]] = {}
    batch_size = 100
    total = len(msg_ids)

    for i in range(0, total, batch_size):
        batch = msg_ids[i : i + batch_size]
        msg_set = ",".join(uid.decode() for uid in batch)

        _typ, fetch_data = conn.fetch(msg_set, f"(BODY.PEEK[HEADER.FIELDS ({header_list})])")

        for item in fetch_data:
            if not isinstance(item, tuple):
                continue
            raw_headers = item[1]
            if not isinstance(raw_headers, bytes):
                continue

            msg = _HEADER_PARSER.parsebytes(raw_headers)

            # Standard address headers.
            for hdr_name in headers:
                for value in msg.get_all(hdr_name, []):
                    for addr in _extract_addresses(value):
                        aliases.setdefault(addr, set()).add(hdr_name)

            # Received headers -- look for "for <alias>" pattern.
            for received in msg.get_all("Received", []):
                for addr in parse_received_for(received):
                    aliases.setdefault(addr, set())

        if progress is not None:
            progress(min(i + batch_size, total), total)

    # Domain filter.
    if domain:
        domain_suffix = f"@{domain.lower()}"
        aliases = {a: hdrs for a, hdrs in aliases.items() if a.endswith(domain_suffix)}

    return aliases


def build_alias_mapping(
    aliases: dict[str, set[str]],
    folder_prefix: str = "alias",
) -> dict[str, Any]:
    """Build a config-compatible JSON structure from discovered aliases.

    Each alias gets a folder ``<folder_prefix>/<local-part>``.  Aliases that
    share the same local part (but differ in ``+`` suffix) are merged into one
    rule.  Per-alias discovered headers are stored per rule.
    """
    # Group aliases by target folder.
    folder_map: dict[str, list[str]] = defaultdict(list)
    for alias in sorted(aliases):
        local = alias.split("@")[0]
        # Strip +suffix to group variants together.
        base_local = local.split("+")[0]
        folder = f"{folder_prefix}/{base_local}"
        folder_map[folder].append(alias)

    rules = []
    for folder, addrs in sorted(folder_map.items()):
        rule_headers: set[str] = set()
        for addr in addrs:
            rule_headers.update(aliases.get(addr, set()))
        rule: dict[str, Any] = {"folder": folder}
        if len(addrs) > 1:
            rule["aliases"] = addrs
        else:
            rule["alias"] = addrs[0]
        if rule_headers:
            rule["headers"] = sorted(rule_headers)
        rules.append(rule)
    return {
        "script_name": "alias-router",
        "headers": ["X-Original-To", "Delivered-To"],
        "use_create": False,
        "match_type": "is",
        "rules": rules,
    }


def write_alias_mapping(mapping: dict[str, Any], output: Path | None) -> str:
    """Serialize *mapping* as JSON. Write to *output* if given, else return string."""
    text = json.dumps(mapping, indent=2, ensure_ascii=False) + "\n"
    if output is not None:
        output.write_text(text, encoding="utf-8", newline="\n")
    return text


def load_alias_file(path: Path) -> dict[str, Any]:
    """Load an existing alias-file (JSON), returning its data dict."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def merge_aliases_into(
    existing: dict[str, Any],
    new_aliases: dict[str, set[str]],
    folder_prefix: str = "alias",
) -> dict[str, Any]:
    """Merge *new_aliases* into an existing alias-file dict.

    Aliases already present in existing rules are skipped.
    New aliases are added to existing rules when the target folder matches,
    or appended as new rules otherwise.
    """
    # Collect all aliases already covered.
    known: set[str] = set()
    for rule in existing.get("rules", []):
        if "alias" in rule:
            known.add(rule["alias"].lower())
        for a in rule.get("aliases", []):
            known.add(a.lower())

    truly_new = {a: hdrs for a, hdrs in new_aliases.items() if a.lower() not in known}
    if not truly_new:
        return existing

    new_mapping = build_alias_mapping(truly_new, folder_prefix=folder_prefix)

    # Build index of existing rules by folder.
    rules = existing.setdefault("rules", [])
    folder_idx: dict[str, int] = {}
    for i, rule in enumerate(rules):
        f = rule.get("folder", "")
        if f not in folder_idx:
            folder_idx[f] = i

    for new_rule in new_mapping["rules"]:
        folder = new_rule["folder"]
        new_addrs = new_rule.get("aliases", [])
        if not new_addrs:
            new_addrs = [new_rule["alias"]] if "alias" in new_rule else []

        if folder in folder_idx:
            # Merge into existing rule.
            target = rules[folder_idx[folder]]
            # Normalize existing rule to use "aliases" list.
            if "alias" in target and "aliases" not in target:
                target["aliases"] = [target.pop("alias")]
            target.setdefault("aliases", []).extend(new_addrs)
        else:
            folder_idx[folder] = len(rules)
            rules.append(new_rule)

    return existing


def update_last_fetched(data: dict[str, Any], fetched_date: date) -> None:
    """Update the ``last_fetched`` field to *fetched_date* (ISO format)."""
    data["last_fetched"] = fetched_date.isoformat()


def get_last_fetched(data: dict[str, Any]) -> date | None:
    """Return the ``last_fetched`` date from alias-file data, or ``None``."""
    raw = data.get("last_fetched")
    if raw is None:
        return None
    return date.fromisoformat(raw)


def stderr_progress(processed: int, total: int) -> None:
    """Print a progress line to stderr, overwriting the previous one."""
    pct = processed * 100 // total if total else 100
    bar_len = 30
    filled = bar_len * processed // total if total else bar_len
    bar = "#" * filled + "-" * (bar_len - filled)
    sys.stderr.write(f"\r  [{bar}] {pct:3d}% ({processed}/{total})")
    sys.stderr.flush()
    if processed >= total:
        sys.stderr.write("\n")
