"""RFC 5230 ``vacation`` (auto-reply) feature.

Independent module: delete this file to disable the feature.

Configuration (TOML, per target)::

    [targets.features.vacation]
    enabled    = true
    subject    = "Out of office"
    body       = "I am away until ..."
    body_file  = "/path/to/message.txt"   # alternative to body
    days       = 7                         # min interval between replies
    addresses  = ["me@x.com", "me@y.com"] # my own addresses (optional)
    from_addr  = "me@x.com"               # explicit From: header (optional)
    handle     = "ooo-2026"               # vacation handle (optional)

If both ``body`` and ``body_file`` are present, ``body_file`` wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def emit_sieve(target: Any, _alias_config: Any) -> tuple[str, set[str]] | None:
    """Return the vacation block + required capabilities, or None if disabled."""
    cfg = target.feature_block("vacation")
    if not cfg or not cfg.get("enabled", False):
        return None

    body: str | None = None
    body_file = cfg.get("body_file")
    if body_file:
        body = Path(body_file).read_text(encoding="utf-8")
    elif cfg.get("body"):
        body = str(cfg["body"])
    if not body:
        # Misconfigured but don't crash the whole pipeline; emit a comment.
        return ("# vacation: enabled but no body/body_file configured -- skipped.", set())

    pieces: list[str] = ["vacation"]
    if "days" in cfg:
        pieces.append(f":days {int(cfg['days'])}")
    if cfg.get("subject"):
        pieces.append(f':subject "{_escape(str(cfg["subject"]))}"')
    if cfg.get("addresses"):
        addrs = ", ".join(f'"{_escape(a)}"' for a in cfg["addresses"])
        pieces.append(f":addresses [{addrs}]")
    if cfg.get("from_addr"):
        pieces.append(f':from "{_escape(str(cfg["from_addr"]))}"')
    if cfg.get("handle"):
        pieces.append(f':handle "{_escape(str(cfg["handle"]))}"')

    pieces.append(f"text:\n{body.rstrip()}\n.\n;")
    block = "# Vacation auto-reply (RFC 5230)\n" + " ".join(pieces[:-1]) + " " + pieces[-1]
    return block, {"vacation"}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
