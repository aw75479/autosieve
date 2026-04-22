"""CLI entry point for autosieve -- subcommands: generate, extract, upload."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import getpass
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from mailfilter.config import load_alias_config
from mailfilter.imap_alias import (
    apply_rules_imap,
    build_alias_mapping,
    collect_known_aliases,
    connect_imap,
    extract_aliases,
    filter_new_aliases,
    get_last_fetched,
    load_alias_file,
    merge_aliases_into,
    stderr_progress,
    update_last_fetched,
    write_alias_mapping,
)
from mailfilter.managesieve import upload_via_managesieve
from mailfilter.server_config import load_server_config
from mailfilter.sieve import generate_sieve

try:
    import keyring as _keyring  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _keyring = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level defaults (single source of truth for all hardcoded values)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_FILE: Path = Path("mailfilter.toml")
"""Auto-discovered server config file name."""

DEFAULT_ALIAS_FILE: str = "aliases.json"
"""Fallback alias file name when not specified via CLI or config."""

DEFAULT_SIEVE_FILE: str = "aliasfilter.sieve"
"""Default output sieve script file name."""

DEFAULT_IMAP_PORT: int = 993
"""Standard IMAP over TLS port."""

DEFAULT_MS_PORT: int = 4190
"""Standard ManageSieve port."""

DEFAULT_IMAP_SECURITY: str = "ssl"
"""Default IMAP connection security (implicit TLS)."""

DEFAULT_MS_SECURITY: str = "ssl"
"""Default ManageSieve connection security (implicit TLS)."""

DEFAULT_FOLDER_PREFIX: str = "alias"
"""Default IMAP folder prefix for alias sub-folders."""

DEFAULT_FOLDERS: list[str] = ["INBOX"]
"""IMAP folders to scan when none are configured."""

DEFAULT_HEADERS: tuple[str, ...] = ("To", "Delivered-To", "X-Original-To")
"""Message headers to inspect when extracting alias addresses."""

DEFAULT_FOLDER_SEP: str = "."
"""Folder hierarchy separator (use '.' for servers that don't support '/')."""

_SECURITY_CHOICES: list[str] = ["ssl", "starttls", "none"]
"""Allowed values for --connection-security flags."""


def eprint(*args: object) -> None:
    """Print *args* to stderr."""
    print(*args, file=sys.stderr)


def _parse_host_port(value: str, default_port: int) -> tuple[str, int]:
    """Parse ``host`` or ``host:port`` into (host, port)."""
    if ":" in value:
        host, port_str = value.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            pass
    return value, default_port


def _prompt(label: str) -> str:
    """Prompt user on stderr, read from stdin."""
    sys.stderr.write(f"{label}: ")
    sys.stderr.flush()
    return input()


def _keyring_key(protocol: str, user: str, host: str) -> str:
    """Build a keyring username key like ``imap://user@host``."""
    return f"{protocol}://{user}@{host}"


def resolve_password(
    password: str | None = None,
    keyring_service: str | None = None,
    keyring_user: str | None = None,
    prompt: str = "Password: ",
    store_in_keyring: bool = False,
) -> str:
    """Resolve password: direct value > keyring > interactive prompt.

    When *store_in_keyring* is True the resolved password is saved to the
    system keyring for future use (requires the ``keyring`` package).
    """
    if password:
        if store_in_keyring and _keyring and keyring_service and keyring_user:
            try:
                _keyring.set_password(keyring_service, keyring_user, password)
            except Exception as exc:  # pragma: no cover - backend-specific
                eprint(f"Warning: keyring store failed for {keyring_user}: {exc}")
        elif store_in_keyring and not _keyring:
            eprint("Warning: keyring package not available; cannot store password.")
        return password

    if _keyring and keyring_service and keyring_user:
        try:
            stored = _keyring.get_password(keyring_service, keyring_user)
        except Exception as exc:  # pragma: no cover - backend-specific
            eprint(f"Warning: keyring lookup failed for {keyring_user}: {exc}")
            stored = None
        if stored:
            return stored

    pw = getpass.getpass(prompt)
    if store_in_keyring and _keyring and keyring_service and keyring_user:
        try:
            _keyring.set_password(keyring_service, keyring_user, pw)
        except Exception as exc:  # pragma: no cover - backend-specific
            eprint(f"Warning: keyring store failed for {keyring_user}: {exc}")
    elif store_in_keyring and not _keyring:
        eprint("Warning: keyring package not available; cannot store password.")
    return pw


def write_output(script_text: str, output_path: Path | None) -> None:
    """Write *script_text* to *output_path*, or to stdout when *output_path* is ``None``."""
    if output_path is None:
        sys.stdout.write(script_text)
        return
    output_path.write_text(script_text, encoding="utf-8", newline="\n")


# -- password arguments (shared) --


def _add_password_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--password", help="Password (prompted if omitted)")
    parser.add_argument("--store-password", action="store_true", help="Store password in system keyring for future use (requires keyring package)")


# -- generate subcommand --


def _add_generate_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("generate", help="Generate a Sieve script from a JSON alias file.")
    p.add_argument("alias_file", metavar="alias-file", nargs="?", type=Path, help="JSON alias file (default: from config or aliases.json)")
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--output", type=Path, help="Output file (default: mailfilter.sieve)")
    p.add_argument("--stdout", action="store_true", help="Write to stdout instead of a file")
    p.add_argument("--script-name", help="Override script name from alias file")
    p.add_argument("--dry-run", action="store_true", help="Show diff against existing output without writing")

    upload = p.add_argument_group("ManageSieve upload (only with --upload)")
    upload.add_argument("--upload", action="store_true", help="Upload via ManageSieve after generation")
    upload.add_argument("--no-activate", action="store_true", help="Upload but do not activate")
    upload.add_argument("--no-check", action="store_true", help="Skip CHECKSCRIPT before upload")
    upload.add_argument("--host", help="ManageSieve host[:port] (default port: 4190)")
    upload.add_argument("--username", help="ManageSieve username")
    upload.add_argument("--authz-id", default="", help="Optional SASL authorization ID")
    upload.add_argument(
        "--connection-security",
        choices=_SECURITY_CHOICES,
        default=None,
        help="Connection security (default: ssl). Options: ssl, starttls, none.",
    )
    _add_password_args(upload)
    p.set_defaults(func=_cmd_generate)


def _cmd_generate(args: argparse.Namespace) -> int:
    # Auto-load config.
    config_path = args.config or (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None)

    srv = None
    if config_path:
        try:
            srv = load_server_config(config_path)
        except Exception as exc:
            eprint(f"Config error: {exc}")
            return 2

    alias_file = args.alias_file or Path(srv.filenames.alias_file if srv else DEFAULT_ALIAS_FILE)
    try:
        config = load_alias_config(alias_file)
    except Exception as exc:
        eprint(f"Alias file error: {exc}")
        return 2

    if args.script_name:
        config.script_name = args.script_name.strip()

    script_text = generate_sieve(config)

    # Determine output destination.
    if args.stdout:
        output_path = None
    elif args.output:
        output_path = args.output
    elif srv and srv.filenames.sieve_file:
        output_path = Path(srv.filenames.sieve_file)
    else:
        output_path = Path(DEFAULT_SIEVE_FILE)

    # Dry-run: show diff and exit.
    if args.dry_run:
        if output_path and output_path.exists():
            old_lines = output_path.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = script_text.splitlines(keepends=True)
            diff = difflib.unified_diff(old_lines, new_lines, fromfile=str(output_path), tofile="(new)")
            diff_text = "".join(diff)
            if diff_text:
                sys.stdout.write(diff_text)
            else:
                eprint("No changes.")
        else:
            sys.stdout.write(script_text)
        return 0

    write_output(script_text, output_path)

    active_count = sum(1 for r in config.rules if r.active)
    inactive_count = len(config.rules) - active_count
    msg = f"Generated {active_count} active rule(s)"
    if inactive_count:
        msg += f" ({inactive_count} inactive skipped)"
    msg += f" for script {config.script_name!r}."
    eprint(msg)
    if output_path:
        eprint(f"Wrote: {output_path}")

    if not args.upload:
        return 0
    return _upload_script(script_name=config.script_name, script_text=script_text, args=args, srv=srv)


def _upload_script(
    *,
    script_name: str,
    script_text: str,
    args: argparse.Namespace,
    srv,
) -> int:
    """Upload a script to ManageSieve and print resulting server status."""
    ms = srv.managesieve if srv else None
    imap_cfg = srv.imap if srv else None
    host_raw = args.host or (ms.host if ms and ms.host else None) or _prompt("ManageSieve host[:port]")
    default_port = ms.port if ms else DEFAULT_MS_PORT
    host, port = _parse_host_port(host_raw, default_port)
    username = args.username or (ms.username if ms else None) or _prompt("ManageSieve username")
    connection_security = args.connection_security or (ms.connection_security if ms else DEFAULT_MS_SECURITY)
    if connection_security == "none":
        eprint("Warning: ManageSieve connection security is 'none': credentials will be transmitted unencrypted.")
    authz_id = args.authz_id or (ms.authz_id if ms else "")
    store_pw = bool(getattr(args, "store_password", False) or (ms.store_password if ms else False))

    # Shared-password mode: reuse the IMAP keyring entry for ManageSieve.
    use_shared = bool(ms and ms.use_imap_password and imap_cfg and imap_cfg.host)
    if use_shared:
        kr_user = _keyring_key("imap", imap_cfg.user, imap_cfg.host)  # type: ignore[union-attr]
        pw = args.password or (imap_cfg.password if imap_cfg else None) or (ms.password if ms else None)
        prompt_text = "IMAP/ManageSieve password: "
    else:
        kr_user = _keyring_key("managesieve", username, host)
        pw = args.password or (ms.password if ms else None)
        prompt_text = "ManageSieve password: "

    try:
        scripts = upload_via_managesieve(
            host=host,
            port=port,
            username=username,
            password=resolve_password(pw, keyring_service="mailfilter", keyring_user=kr_user, prompt=prompt_text, store_in_keyring=store_pw),
            script_name=script_name,
            script_text=script_text,
            connection_security=connection_security,
            authz_id=authz_id,
            do_check=not args.no_check,
            activate=not args.no_activate,
        )
    except Exception as exc:
        eprint(f"Upload failed: {exc}")
        return 1

    active = next((name for name, is_active in scripts if is_active), None)
    eprint("ManageSieve upload complete.")
    if scripts:
        eprint("Server scripts:")
        for name, is_active in scripts:
            marker = " *ACTIVE*" if is_active else ""
            eprint(f"  - {name}{marker}")
    if active is not None:
        eprint(f"Active script: {active!r}")
    return 0


# -- extract subcommand --


def _add_extract_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("extract", help="Scan an IMAP inbox and discover email aliases from message headers.")
    p.add_argument("server", nargs="?", help="[required] IMAP server as host or host:port (default port: 993)")
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--user", help="[required] IMAP username (prompted if omitted)")
    p.add_argument("--domain", help="[required] Only extract aliases matching this domain (e.g. company.com)")
    p.add_argument("--folder", nargs="+", dest="folders", help="IMAP folder(s) to scan (default: INBOX). May specify multiple.")
    p.add_argument("--limit", type=int, help="Scan at most N messages per folder (most recent first)")
    p.add_argument("--since", help="Only scan messages from this date onwards (YYYY-MM-DD)")
    p.add_argument("--headers", nargs="+", help="Headers to extract aliases from (default: To Delivered-To X-Original-To)")
    p.add_argument("--folder-prefix", help="Folder prefix for alias rules (default: alias)")
    p.add_argument("alias_file", metavar="alias-file", nargs="?", type=Path, help="JSON alias file to write/update (default: aliases.json)")
    p.add_argument("--connection-security", choices=_SECURITY_CHOICES, help="Connection security for IMAP (default: ssl)")
    p.add_argument("--no-incremental", action="store_true", help="Disable incremental scanning (ignore last_fetched date)")
    p.add_argument("--dry-run", action="store_true", help="Show what aliases would be added without writing")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output: print all discovered aliases and headers")
    _add_password_args(p)
    p.add_argument("--stdout", action="store_true", help="Write to stdout instead of a file")
    p.set_defaults(func=_cmd_extract)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_extract(args: argparse.Namespace) -> int:
    # Auto-load config.
    config_path = args.config or (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None)

    srv = None
    if config_path:
        try:
            srv = load_server_config(config_path)
        except Exception as exc:
            eprint(f"Config error: {exc}")
            return 2

    imap_cfg = srv.imap if srv else None

    # Resolve parameters: CLI > server config > interactive prompt.
    server_raw = args.server or (imap_cfg.host if imap_cfg and imap_cfg.host else None) or _prompt("IMAP server (host or host:port)")
    default_port = imap_cfg.port if imap_cfg else DEFAULT_IMAP_PORT
    host, port = _parse_host_port(server_raw, default_port)
    user = args.user or (imap_cfg.user if imap_cfg else None) or _prompt("IMAP username")
    domain = args.domain or (imap_cfg.domain if imap_cfg and imap_cfg.domain else None) or _prompt("Domain filter (e.g. company.com)")
    domain = domain.lstrip("@")

    folders = args.folders or (imap_cfg.folders if imap_cfg else DEFAULT_FOLDERS)
    connection_security = args.connection_security or (imap_cfg.connection_security if imap_cfg else DEFAULT_IMAP_SECURITY)
    if connection_security == "none":
        eprint("Warning: IMAP connection security is 'none': credentials will be transmitted unencrypted.")
    headers = tuple(args.headers or (imap_cfg.headers if imap_cfg else list(DEFAULT_HEADERS)))
    folder_prefix = args.folder_prefix or (srv.managesieve.folder_prefix if srv else DEFAULT_FOLDER_PREFIX)
    folder_sep = getattr(imap_cfg, "folder_sep", DEFAULT_FOLDER_SEP) if imap_cfg else DEFAULT_FOLDER_SEP
    pw = args.password or (imap_cfg.password if imap_cfg else None)
    store_pw = bool(getattr(args, "store_password", False) or (imap_cfg.store_password if imap_cfg else False))

    # Incremental: check config and CLI flag.
    incremental = (imap_cfg.incremental if imap_cfg else True) and not args.no_incremental

    # Determine output.
    existing_data: dict | None = None
    output_path: Path | None = args.alias_file
    if output_path is None and not args.stdout:
        output_path = Path(srv.filenames.alias_file if srv else DEFAULT_ALIAS_FILE)
    since = _parse_date(args.since) if args.since else None

    if output_path and output_path.exists():
        try:
            existing_data = load_alias_file(output_path)
            if incremental:
                stored_since = get_last_fetched(existing_data)
                if since is None and stored_since is not None:
                    # Overlap by 1 day to avoid missing messages at the boundary.
                    since = stored_since - timedelta(days=1)
                    eprint(f"Incremental update: fetching since {since.isoformat()}")
        except Exception as exc:
            eprint(f"Warning: could not read existing alias file: {exc}")

    try:
        password = resolve_password(
            pw,
            keyring_service="mailfilter",
            keyring_user=_keyring_key("imap", user, host),
            prompt="IMAP password: ",
            store_in_keyring=store_pw,
        )
        conn = connect_imap(host=host, port=port, user=user, password=password, connection_security=connection_security)
    except Exception as exc:
        eprint(f"IMAP connection failed: {exc}")
        return 1

    try:
        # Multi-folder scanning: iterate over all folders and merge results.
        all_aliases: dict[str, set[str]] = {}
        for folder in folders:
            eprint(f"Scanning {folder} on {host}...")
            folder_aliases = extract_aliases(conn, folder=folder, domain=domain, headers=headers, limit=args.limit, since=since, progress=stderr_progress)
            for addr, hdrs in folder_aliases.items():
                all_aliases.setdefault(addr, set()).update(hdrs)
        aliases = all_aliases
    except Exception as exc:
        eprint(f"Extraction failed: {exc}")
        return 1
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

    if not aliases:
        eprint("No aliases found.")
        return 0

    eprint(f"Found {len(aliases)} alias(es).")
    if args.verbose:
        for alias in sorted(aliases):
            sorted_hdrs = sorted(aliases[alias])
            hdr_text = ", ".join(sorted_hdrs) if sorted_hdrs else "(received-only)"
            eprint(f"  found: {alias} [{hdr_text}]")

    today = date.today()
    new_aliases = aliases

    if existing_data is not None:
        existing_data.setdefault("generation_mode", "envelope")
        existing_data.setdefault("folder_prefix", folder_prefix)
        existing_data.setdefault("catch_all_folder", f"{folder_prefix}/_other")
        known_before = collect_known_aliases(existing_data)
        new_aliases = filter_new_aliases(existing_data, aliases)

        if new_aliases:
            eprint(f"New aliases: {len(new_aliases)}")
            for alias in sorted(new_aliases):
                eprint(f"  + {alias}")
        else:
            eprint(f"No new aliases to add ({len(known_before)} already known).")

        merge_aliases_into(existing_data, aliases, folder_prefix=folder_prefix, folder_sep=folder_sep)
        update_last_fetched(existing_data, today)
        result_data = existing_data
    else:
        eprint(f"New aliases: {len(new_aliases)}")
        for alias in sorted(new_aliases):
            eprint(f"  + {alias}")
        result_data = build_alias_mapping(aliases, folder_prefix=folder_prefix, folder_sep=folder_sep)
        update_last_fetched(result_data, today)

    # Dry-run: show what would be written without writing.
    if args.dry_run:
        text = write_alias_mapping(result_data, None)
        if output_path and output_path.exists():
            old_lines = output_path.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = text.splitlines(keepends=True)
            diff = difflib.unified_diff(old_lines, new_lines, fromfile=str(output_path), tofile="(new)")
            diff_text = "".join(diff)
            if diff_text:
                sys.stdout.write(diff_text)
            else:
                eprint("No changes.")
        else:
            sys.stdout.write(text)
        return 0

    text = write_alias_mapping(result_data, output_path)

    if output_path is None:
        sys.stdout.write(text)
    else:
        eprint(f"Wrote: {output_path}")

    return 0


# -- upload subcommand --


def _add_upload_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("upload", help="Upload an existing Sieve script via ManageSieve and optionally activate it.")
    p.add_argument("script_file", nargs="?", type=Path, help="Sieve file to upload (default from config filenames.sieve_file or aliasfilter.sieve)")
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--script-name", help="Script name on server (default: file stem)")
    p.add_argument("--no-activate", action="store_true", help="Upload but do not activate")
    p.add_argument("--no-check", action="store_true", help="Skip CHECKSCRIPT before upload")
    p.add_argument("--host", help="ManageSieve host[:port] (default port: 4190)")
    p.add_argument("--username", help="ManageSieve username")
    p.add_argument("--authz-id", default="", help="Optional SASL authorization ID")
    p.add_argument(
        "--connection-security",
        choices=_SECURITY_CHOICES,
        default=None,
        help="Connection security (default: ssl). Options: ssl, starttls, none.",
    )
    _add_password_args(p)
    p.set_defaults(func=_cmd_upload)


def _cmd_upload(args: argparse.Namespace) -> int:
    config_path = args.config or (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None)

    srv = None
    if config_path:
        try:
            srv = load_server_config(config_path)
        except Exception as exc:
            eprint(f"Config error: {exc}")
            return 2

    if args.script_file:
        script_path = args.script_file
    elif srv and srv.filenames.sieve_file:
        script_path = Path(srv.filenames.sieve_file)
    else:
        script_path = Path(DEFAULT_SIEVE_FILE)

    if not script_path.exists():
        eprint(f"Script file not found: {script_path}")
        return 2

    try:
        script_text = script_path.read_text(encoding="utf-8")
    except Exception as exc:
        eprint(f"Script file error: {exc}")
        return 2

    script_name = args.script_name or script_path.stem
    return _upload_script(script_name=script_name, script_text=script_text, args=args, srv=srv)


# -- apply subcommand --


def _add_apply_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "apply",
        help="Apply alias rules to existing IMAP messages, moving them to their target folders.",
    )
    p.add_argument("alias_file", metavar="alias-file", nargs="?", type=Path, help="JSON alias file (default: from config or aliases.json)")
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--folder", nargs="+", dest="folders", help="Source IMAP folder(s) to scan (default: from config or INBOX)")
    p.add_argument("--host", help="IMAP host[:port] (default port: 993)")
    p.add_argument("--user", help="IMAP username")
    p.add_argument(
        "--connection-security",
        choices=_SECURITY_CHOICES,
        default=None,
        help="Connection security (default: ssl). Options: ssl, starttls, none.",
    )
    p.add_argument("--dry-run", action="store_true", help="Show what would be moved without actually moving")
    p.add_argument("--no-create", action="store_true", help="Do not create target folders if they do not exist")
    _add_password_args(p)
    p.set_defaults(func=_cmd_apply)


def _cmd_apply(args: argparse.Namespace) -> int:
    config_path = args.config or (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None)

    srv = None
    if config_path:
        try:
            srv = load_server_config(config_path)
        except Exception as exc:
            eprint(f"Config error: {exc}")
            return 2

    imap_cfg = srv.imap if srv else None

    alias_file = args.alias_file or Path(srv.filenames.alias_file if srv else DEFAULT_ALIAS_FILE)
    try:
        config = load_alias_config(alias_file)
    except Exception as exc:
        eprint(f"Alias file error: {exc}")
        return 2

    server_raw = args.host or (imap_cfg.host if imap_cfg and imap_cfg.host else None) or _prompt("IMAP host[:port]")
    default_port = imap_cfg.port if imap_cfg else DEFAULT_IMAP_PORT
    host, port = _parse_host_port(server_raw, default_port)
    user = args.user or (imap_cfg.user if imap_cfg else None) or _prompt("IMAP username")
    connection_security = args.connection_security or (imap_cfg.connection_security if imap_cfg else DEFAULT_IMAP_SECURITY)
    if connection_security == "none":
        eprint("Warning: IMAP connection security is 'none': credentials will be transmitted unencrypted.")
    folders = args.folders or (imap_cfg.folders if imap_cfg else DEFAULT_FOLDERS)

    pw = args.password or (imap_cfg.password if imap_cfg else None)
    store_pw = bool(getattr(args, "store_password", False) or (imap_cfg.store_password if imap_cfg else False))

    try:
        password = resolve_password(
            pw,
            keyring_service="mailfilter",
            keyring_user=_keyring_key("imap", user, host),
            prompt="IMAP password: ",
            store_in_keyring=store_pw,
        )
        conn = connect_imap(host=host, port=port, user=user, password=password, connection_security=connection_security)
    except Exception as exc:
        eprint(f"IMAP connection failed: {exc}")
        return 1

    active_rules = sum(1 for r in config.rules if r.active)
    action_word = "Would move" if args.dry_run else "Moving"
    eprint(f"{action_word} messages for {active_rules} active rule(s) across {len(folders)} folder(s)...")

    def _progress(folder: str, count: int) -> None:
        if count > 0:
            verb = "would move" if args.dry_run else "moved"
            eprint(f"  {verb} {count} → {folder}")

    try:
        moved = apply_rules_imap(
            conn,
            config,
            folders,
            dry_run=args.dry_run,
            create_folders=not args.no_create,
            progress=_progress,
        )
    except Exception as exc:
        eprint(f"Apply failed: {exc}")
        return 1
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

    total = sum(moved.values())
    verb = "Would move" if args.dry_run else "Moved"
    eprint(f"{verb} {total} message(s) to {len(moved)} folder(s).")
    return 0


# -- top-level parser --


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="autosieve",
        description="Generate Sieve scripts from alias mappings and extract aliases from IMAP.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_generate_parser(subparsers)
    _add_extract_parser(subparsers)
    _add_upload_parser(subparsers)
    _add_apply_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``autosieve`` / ``mailfilter`` CLI.

    Args:
        argv: Argument list (uses :data:`sys.argv` when ``None``).

    Returns:
        Shell exit code.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
