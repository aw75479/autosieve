"""Optional feature modules for autosieve.

Every module in this package is **independent and removable** -- delete the
file to disable the feature.  Modules are loaded lazily by ``cli.py`` (and
by ``commands.sync``) via :func:`importlib.import_module`, so a missing
file simply means the feature is unavailable.

Convention for a sieve-emitting feature module
==============================================

Each module should expose::

    def emit_sieve(target, alias_config) -> tuple[str, set[str]] | None:
        '''Return (sieve_block, required_capabilities) or None if disabled.

        sieve_block: text appended verbatim after the generated rules.
        required_capabilities: extra Sieve capabilities to add to the
            top-level require [...] statement (e.g. {"vacation"}).
        '''

The return value is composed by :func:`merge_features` into the final
script text.

Currently provided
==================

* :mod:`autosieve.features.vacation` -- RFC 5230 vacation auto-reply.
* :mod:`autosieve.features.notify` -- RFC 5435 enotify helper.
* :mod:`autosieve.features.custom_filters` -- TOML-driven custom filters
  on From / Subject / arbitrary headers / body.
* :mod:`autosieve.features.oauth2` -- XOAUTH2 token resolution for IMAP
  and ManageSieve auth.

The :mod:`autosieve.features.tags` extension is implemented in
:mod:`autosieve.config` (Rule.tags field) and :mod:`autosieve.cli`
(``--tag`` filter); it does not need a separate feature module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Module-level regex for parsing the leading require statement.
# Matches both ``require "fileinto";`` and ``require ["a", "b"];`` forms.
_REQUIRE_RE = re.compile(
    r'^require\s+(?:"(?P<single>[^"]+)"|\[(?P<list>[^\]]*)\])\s*;\s*$',
    re.MULTILINE,
)


def merge_features(
    script_text: str,
    extra_blocks: Iterable[str],
    extra_caps: Iterable[str],
) -> str:
    """Inject feature-emitted Sieve blocks and required capabilities.

    Adds *extra_caps* to the existing ``require`` line (deduplicated,
    preserving original order) and appends each non-empty block in
    *extra_blocks* after the original script text.
    """
    blocks = [b.rstrip() for b in extra_blocks if b and b.strip()]
    caps = list(dict.fromkeys(extra_caps))  # de-dup, keep order
    if not blocks and not caps:
        return script_text

    if caps:
        script_text = _augment_require(script_text, caps)

    if blocks:
        if not script_text.endswith("\n"):
            script_text += "\n"
        script_text += "\n# --- autosieve features ---\n"
        script_text += "\n\n".join(blocks)
        script_text += "\n"
    return script_text


def _augment_require(script_text: str, extra_caps: list[str]) -> str:
    """Append capabilities to the first ``require`` directive in *script_text*."""
    match = _REQUIRE_RE.search(script_text)
    if not match:
        # No existing require line; insert one at the very top.
        line = "require [" + ", ".join(f'"{c}"' for c in extra_caps) + "];\n"
        return line + script_text

    if match.group("single") is not None:
        existing = [match.group("single")]
    else:
        raw = match.group("list") or ""
        existing = [c.strip().strip('"') for c in raw.split(",") if c.strip()]

    merged = existing[:]
    for cap in extra_caps:
        if cap not in merged:
            merged.append(cap)

    if merged == existing:
        return script_text

    new_line = "require [" + ", ".join(f'"{c}"' for c in merged) + "];"
    return script_text[: match.start()] + new_line + script_text[match.end() :]
