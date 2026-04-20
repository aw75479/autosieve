"""CLI entry point for mailfilter -- subcommands: generate, extract-aliases."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from mailfilter.config import load_config
from mailfilter.imap_alias import (
    build_alias_mapping,
    connect_imap,
    extract_aliases,
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


def eprint(*args: object) -> None:
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


def resolve_password(
    password: str | None = None,
    prompt: str = "Password: ",
) -> str:
    """Resolve password from direct value or interactive prompt."""
    if password:
        return password
    return getpass.getpass(prompt)


def write_output(script_text: str, output_path: Path | None) -> None:
    if output_path is None:
        sys.stdout.write(script_text)
        return
    output_path.write_text(script_text, encoding="utf-8", newline="\n")


# -- password arguments (shared) --


def _add_password_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--password", help="Password (prompted if omitted)")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")


# -- generate subcommand --


def _add_generate_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "generate",
        help="Generate a Sieve script from a JSON alias file.",
    )
    p.add_argument("alias_file", metavar="alias-file", type=Path, help="[required] JSON alias file")
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--output", type=Path, help="Output file (default: mailfilter.sieve)")
    p.add_argument("--stdout", action="store_true", help="Write to stdout instead of a file")
    p.add_argument("--script-name", help="Override script name from alias file")

    upload = p.add_argument_group("ManageSieve upload (only with --upload)")
    upload.add_argument("--upload", action="store_true", help="Upload via ManageSieve after generation")
    upload.add_argument("--no-activate", action="store_true", help="Upload but do not activate")
    upload.add_argument("--no-check", action="store_true", help="Skip CHECKSCRIPT before upload")
    upload.add_argument("--host", help="ManageSieve host[:port] (default port: 4190)")
    upload.add_argument("--username", help="ManageSieve username")
    upload.add_argument("--authz-id", default="", help="Optional SASL authorization ID")
    upload.add_argument(
        "--connection-security",
        choices=["auto", "ssl", "starttls", "none"],
        default="auto",
        help="Connection security (default: auto). Aligned with Thunderbird: ssl, starttls, none",
    )
    _add_password_args(upload)
    p.set_defaults(func=_cmd_generate)


def _cmd_generate(args: argparse.Namespace) -> int:
    # Auto-load config.
    config_path = args.config
    if config_path is None:
        default_cfg = Path("mailfilter.toml")
        if default_cfg.exists():
            config_path = default_cfg

    srv = None
    if config_path:
        try:
            srv = load_server_config(config_path)
        except Exception as exc:
            eprint(f"Config error: {exc}")
            return 2

    try:
        config = load_config(args.alias_file)
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
    elif srv and srv.output.sieve_file:
        output_path = Path(srv.output.sieve_file)
    else:
        output_path = Path("mailfilter.sieve")
    write_output(script_text, output_path)

    eprint(f"Generated {len(config.rules)} rule(s) for script {config.script_name!r}.")
    if output_path:
        eprint(f"Wrote: {output_path}")

    if not args.upload:
        return 0

    # Prompt for missing mandatory upload args (with server config defaults).
    ms = srv.managesieve if srv else None
    host_raw = args.host or (ms.host if ms and ms.host else None) or _prompt("ManageSieve host[:port]")
    default_port = ms.port if ms else 4190
    host, port = _parse_host_port(host_raw, default_port)
    username = args.username or (ms.username if ms else None) or _prompt("ManageSieve username")
    connection_security = args.connection_security if args.connection_security != "auto" else (ms.connection_security if ms else "auto")
    insecure = args.insecure or (ms.insecure if ms else False)
    authz_id = args.authz_id or (ms.authz_id if ms else "")
    pw = args.password or (ms.password if ms else None)

    try:
        scripts = upload_via_managesieve(
            host=host,
            port=port,
            username=username,
            password=resolve_password(pw, prompt="ManageSieve password: "),
            script_name=config.script_name,
            script_text=script_text,
            connection_security=connection_security,
            insecure=insecure,
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


# -- extract-aliases subcommand --


def _add_extract_aliases_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "extract-aliases",
        help="Scan an IMAP inbox and discover email aliases from message headers.",
    )
    p.add_argument(
        "server",
        nargs="?",
        help="[required] IMAP server as host or host:port (default port: 993)",
    )
    p.add_argument("--config", type=Path, help="Server config TOML file")
    p.add_argument("--user", help="[required] IMAP username (prompted if omitted)")
    p.add_argument(
        "--domain",
        help="[required] Only extract aliases matching this domain (e.g. company.com)",
    )
    p.add_argument("--folder", default="INBOX", help="IMAP folder to scan (default: INBOX)")
    p.add_argument("--limit", type=int, help="Scan at most N messages (most recent first)")
    p.add_argument("--since", help="Only scan messages from this date onwards (YYYY-MM-DD)")
    p.add_argument(
        "--headers",
        nargs="+",
        help="Headers to extract aliases from (default: To Delivered-To X-Original-To)",
    )
    p.add_argument(
        "--folder-prefix",
        help="Folder prefix for alias rules (default: alias)",
    )
    p.add_argument(
        "alias_file",
        metavar="alias-file",
        nargs="?",
        type=Path,
        help="JSON alias file to write/update (default: stdout)",
    )
    p.add_argument(
        "--connection-security",
        choices=["ssl", "starttls", "none"],
        help="Connection security for IMAP (default: ssl). Aligned with Thunderbird: ssl, starttls, none",
    )
    _add_password_args(p)
    p.add_argument("--stdout", action="store_true", help="Write to stdout instead of a file")
    p.set_defaults(func=_cmd_extract_aliases)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_extract_aliases(args: argparse.Namespace) -> int:
    # Auto-load config.
    config_path = args.config
    if config_path is None:
        default_cfg = Path("mailfilter.toml")
        if default_cfg.exists():
            config_path = default_cfg

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
    default_port = imap_cfg.port if imap_cfg else 993
    host, port = _parse_host_port(server_raw, default_port)
    user = args.user or (imap_cfg.user if imap_cfg else None) or _prompt("IMAP username")
    domain = args.domain or (imap_cfg.domain if imap_cfg and imap_cfg.domain else None) or _prompt("Domain filter (e.g. company.com)")
    domain = domain.lstrip("@")

    folder = args.folder or (imap_cfg.folder if imap_cfg else "INBOX")
    connection_security = args.connection_security or (imap_cfg.connection_security if imap_cfg else "ssl")
    insecure = args.insecure or (imap_cfg.insecure if imap_cfg else False)
    default_headers = ["To", "Delivered-To", "X-Original-To"]
    headers = tuple(args.headers or (imap_cfg.headers if imap_cfg else default_headers))
    folder_prefix = args.folder_prefix or (srv.alias.folder_prefix if srv else "alias")
    pw = args.password or (imap_cfg.password if imap_cfg else None)

    # Incremental: if alias-file exists, use last_fetched as since date.
    existing_data: dict | None = None
    output_path: Path | None = args.alias_file
    if output_path is None and not args.stdout:
        output_path = Path(srv.output.alias_file if srv else "aliases.json")
    since = _parse_date(args.since) if args.since else None

    if output_path and output_path.exists():
        try:
            existing_data = load_alias_file(output_path)
            stored_since = get_last_fetched(existing_data)
            if since is None and stored_since is not None:
                # Overlap by 1 day to avoid missing messages at the boundary.
                since = stored_since - timedelta(days=1)
                eprint(f"Incremental update: fetching since {since.isoformat()}")
        except Exception as exc:
            eprint(f"Warning: could not read existing alias file: {exc}")

    try:
        password = resolve_password(pw, prompt="IMAP password: ")
        conn = connect_imap(
            host=host,
            port=port,
            user=user,
            password=password,
            connection_security=connection_security,
            insecure=insecure,
        )
    except Exception as exc:
        eprint(f"IMAP connection failed: {exc}")
        return 1

    try:
        eprint(f"Scanning {folder} on {host}...")
        aliases = extract_aliases(
            conn,
            folder=folder,
            domain=domain,
            headers=headers,
            limit=args.limit,
            since=since,
            progress=stderr_progress,
        )
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

    today = date.today()

    if existing_data is not None:
        merge_aliases_into(existing_data, aliases, folder_prefix=folder_prefix)
        update_last_fetched(existing_data, today)
        text = write_alias_mapping(existing_data, output_path)
    else:
        mapping = build_alias_mapping(aliases, folder_prefix=folder_prefix)
        update_last_fetched(mapping, today)
        text = write_alias_mapping(mapping, output_path)

    if output_path is None:
        sys.stdout.write(text)
    else:
        eprint(f"Wrote: {output_path}")

    return 0


# -- top-level parser --


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailfilter",
        description="Generate Sieve scripts from alias mappings and extract aliases from IMAP.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_generate_parser(subparsers)
    _add_extract_aliases_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
