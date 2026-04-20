"""Extract email aliases from an IMAP inbox by scanning message headers."""

from __future__ import annotations

import email.utils
import imaplib
import json
import re
import ssl
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
    tls_mode: str = "implicit",
    insecure: bool = False,
) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    """Connect and authenticate to an IMAP server."""
    if tls_mode == "implicit":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    elif tls_mode == "starttls":
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
) -> set[str]:
    """Scan messages in *folder* and return a set of discovered alias addresses.

    If *domain* is given, only addresses ending in ``@domain`` are returned.
    If *since* is given, only messages received on or after that date are scanned.
    If *limit* is given, at most *limit* messages are scanned (most recent first).
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
        return set()

    # Most recent first when applying limit.
    msg_ids = list(reversed(msg_ids))
    if limit is not None:
        msg_ids = msg_ids[:limit]

    # Fetch only the headers we need, in batches to avoid IMAP argument length limits.
    fetch_headers = [*headers, "Received"]
    header_list = " ".join(fetch_headers)

    aliases: set[str] = set()
    batch_size = 100

    for i in range(0, len(msg_ids), batch_size):
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
                    aliases.update(_extract_addresses(value))

            # Received headers -- look for "for <alias>" pattern.
            for received in msg.get_all("Received", []):
                aliases.update(parse_received_for(received))

    # Domain filter.
    if domain:
        domain_suffix = f"@{domain.lower()}"
        aliases = {a for a in aliases if a.endswith(domain_suffix)}

    return aliases


def build_alias_mapping(
    aliases: set[str],
    default_folder: str = "INBOX",
) -> dict[str, Any]:
    """Build a config-compatible JSON structure from discovered aliases.

    Each alias gets its own rule with *default_folder*. The user is expected
    to group aliases and set proper folders by editing the output.
    """
    rules = [{"alias": alias, "folder": default_folder} for alias in sorted(aliases)]
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
