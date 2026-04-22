"""Extract email aliases from an IMAP inbox by scanning message headers."""

from __future__ import annotations

import contextlib
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mailfilter.config import Config

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
) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    """Connect and authenticate to an IMAP server.

    Args:
        host: IMAP server hostname.
        port: TCP port (default 993 for implicit TLS).
        user: Login username.
        password: Login password.
        connection_security: One of ``ssl``, ``starttls``, or ``none``.
            Defaults to ``ssl`` (implicit TLS on port 993).  Passing ``none``
            sends credentials in plaintext and should only be used on a
            trusted private network.

    Returns:
        An authenticated :class:`imaplib.IMAP4` or :class:`imaplib.IMAP4_SSL`
        connection.

    Raises:
        imaplib.IMAP4.error: On authentication failure.
        OSError: On network errors.
    """
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL
    if connection_security == "ssl":
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
    elif connection_security == "starttls":
        conn = imaplib.IMAP4(host, port)
        conn.starttls(ssl.create_default_context())
    else:  # none
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
    folder_sep: str = ".",
) -> dict[str, Any]:
    """Build a config-compatible JSON structure from discovered aliases.

    Each alias gets a folder ``<folder_prefix><folder_sep><local-part>``.
    Aliases that share the same local part (but differ in ``+`` suffix) are
    merged into one rule.  Per-alias discovered headers are stored per rule.

    Args:
        aliases: Mapping of alias address to the set of header names it was
            found in (empty set = received-only).
        folder_prefix: Leading path component for generated folder names.
        folder_sep: Folder hierarchy separator used by the mail server
            (``"/"`` or ``"."``).

    Returns:
        A dict that can be serialised to JSON as an alias file.
    """
    # Group aliases by target folder.
    folder_map: dict[str, list[str]] = defaultdict(list)
    for alias in sorted(aliases):
        local = alias.split("@")[0]
        # Strip +suffix to group variants together.
        base_local = local.split("+")[0]
        folder = f"{folder_prefix}{folder_sep}{base_local}"
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
        "generation_mode": "envelope",
        "folder_prefix": folder_prefix,
        "folder_sep": folder_sep,
        "catch_all_folder": f"{folder_prefix}{folder_sep}_other",
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


def collect_known_aliases(existing: dict[str, Any]) -> set[str]:
    """Return all aliases already present in an alias-file dict."""
    known: set[str] = set()
    for rule in existing.get("rules", []):
        if "alias" in rule:
            known.add(str(rule["alias"]).lower())
        for alias in rule.get("aliases", []):
            known.add(str(alias).lower())
    return known


def filter_new_aliases(existing: dict[str, Any], discovered: dict[str, set[str]]) -> dict[str, set[str]]:
    """Return only discovered aliases not yet present in *existing*."""
    known = collect_known_aliases(existing)
    return {addr: hdrs for addr, hdrs in discovered.items() if addr.lower() not in known}


def merge_aliases_into(
    existing: dict[str, Any],
    new_aliases: dict[str, set[str]],
    folder_prefix: str = "alias",
    folder_sep: str = "/",
) -> dict[str, Any]:
    """Merge *new_aliases* into an existing alias-file dict.

    Aliases already present in existing rules are skipped.
    New aliases are added to existing rules when the target folder matches,
    or appended as new rules otherwise.

    Args:
        existing: The current alias-file dict (mutated in place).
        new_aliases: Newly discovered aliases to incorporate.
        folder_prefix: Folder prefix used when creating new rules.
        folder_sep: Folder hierarchy separator for new rule paths.

    Returns:
        The mutated *existing* dict.
    """
    truly_new = filter_new_aliases(existing, new_aliases)
    if not truly_new:
        return existing

    new_mapping = build_alias_mapping(truly_new, folder_prefix=folder_prefix, folder_sep=folder_sep)

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


# ---------------------------------------------------------------------------
# Retroactive rule application via IMAP
# ---------------------------------------------------------------------------


def create_imap_folder(conn: imaplib.IMAP4 | imaplib.IMAP4_SSL, folder: str) -> None:
    """Create *folder* on the IMAP server, ignoring 'already exists' errors."""
    typ, data = conn.create(folder)
    if typ != "OK":
        msg = data[0].decode() if data and isinstance(data[0], bytes) else str(data)
        if "ALREADYEXISTS" not in msg.upper() and "already exist" not in msg.lower():
            raise imaplib.IMAP4.error(f"CREATE {folder!r} failed: {msg}")


def _or_imap_search(*parts: str) -> str:
    """Build a nested IMAP OR expression from two or more search-key strings.

    IMAP OR takes exactly two operands; additional operands are nested on the
    left: ``OR (OR A B) C``.  Simple compound keys like ``HEADER "X" "Y"`` do
    not need parentheses; only nested OR sub-expressions do.
    """
    if not parts:
        return "ALL"
    result = parts[0]
    for p in parts[1:]:
        left = f"({result})" if result.startswith("OR ") else result
        result = f"OR {left} {p}"
    return result


def _imap_move_messages(
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    uids: list[bytes],
    target_folder: str,
) -> None:
    """Move messages identified by *uids* to *target_folder*.

    Tries the IMAP MOVE extension (RFC 6851) first; falls back to COPY +
    STORE ``\\Deleted`` + EXPUNGE.
    """
    if not uids:
        return
    uid_str = b",".join(uids).decode()
    # Try MOVE extension.
    try:
        typ, _ = conn.uid("MOVE", uid_str, target_folder)  # type: ignore[call-overload]
        if typ == "OK":
            return
    except (imaplib.IMAP4.error, AttributeError):
        pass
    # Fall back to COPY + mark deleted + expunge.
    typ, data = conn.uid("COPY", uid_str, target_folder)  # type: ignore[call-overload]
    if typ != "OK":
        msg = data[0].decode() if data and isinstance(data[0], bytes) else str(data)
        raise imaplib.IMAP4.error(f"COPY to {target_folder!r} failed: {msg}")
    conn.uid("STORE", uid_str, "+FLAGS", "(\\Deleted)")  # type: ignore[call-overload]
    conn.expunge()


def apply_rules_imap(
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    config: Config,
    source_folders: list[str],
    *,
    dry_run: bool = False,
    create_folders: bool = True,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, int]:
    """Apply alias rules to existing messages in *source_folders*.

    For each active rule in *config*, searches *source_folders* for messages
    matching any of the rule's aliases in the configured headers and moves
    them to the rule's target folder.

    Args:
        conn: Authenticated IMAP connection.
        config: Loaded alias configuration.
        source_folders: IMAP folders to scan (e.g. ``["INBOX"]``).
        dry_run: When ``True``, count matches only without moving.
        create_folders: When ``True``, create target folders that don't exist.
        progress: Optional callback ``(target_folder, matched_count)`` called
            after processing each rule per source folder.

    Returns:
        Dict mapping target folder path to number of messages moved (or
        matched when *dry_run* is ``True``).
    """

    moved: dict[str, int] = defaultdict(int)
    active_rules = [r for r in config.rules if r.active]

    for source in source_folders:
        conn.select(source)

        for rule in active_rules:
            effective_headers: list[str] = rule.headers if rule.headers else list(config.headers)
            criteria_parts: list[str] = []
            for alias in rule.aliases:
                for header in effective_headers:
                    criteria_parts.append(f'HEADER "{header}" "{alias}"')

            if not criteria_parts:
                continue

            search_expr = _or_imap_search(*criteria_parts)
            try:
                typ, data = conn.uid("SEARCH", None, search_expr)  # type: ignore[arg-type,call-overload]
            except imaplib.IMAP4.error:
                continue
            if typ != "OK" or not data or not data[0]:
                continue

            uids = data[0].split() if data[0] else []
            if not uids:
                if progress:
                    progress(rule.folder, 0)
                continue

            if not dry_run:
                if create_folders:
                    with contextlib.suppress(Exception):  # folder exists or server error
                        create_imap_folder(conn, rule.folder)
                _imap_move_messages(conn, uids, rule.folder)
                # Re-select the source folder after a move (EXPUNGE invalidates it).
                conn.select(source)

            moved[rule.folder] += len(uids)
            if progress:
                progress(rule.folder, len(uids))

    return dict(moved)
