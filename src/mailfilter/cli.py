"""CLI entry point for mailfilter -- subcommands: generate, extract-aliases."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from mailfilter.config import load_config
from mailfilter.imap_alias import (
    build_alias_mapping,
    connect_imap,
    extract_aliases,
    write_alias_mapping,
)
from mailfilter.managesieve import upload_via_managesieve
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
    password_env: str | None = None,
    password_file: str | None = None,
    prompt: str = "Password: ",
) -> str:
    """Resolve password from direct value, env var, file, or interactive prompt."""
    if password:
        return password
    if password_env:
        value = os.environ.get(password_env)
        if value is None:
            raise RuntimeError(f"environment variable {password_env!r} is not set")
        return value
    if password_file:
        return Path(password_file).read_text(encoding="utf-8").strip("\r\n")
    return getpass.getpass(prompt)


def write_output(script_text: str, output_path: Path | None) -> None:
    if output_path is None:
        sys.stdout.write(script_text)
        return
    output_path.write_text(script_text, encoding="utf-8", newline="\n")


# -- password arguments (shared) --


def _add_password_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--password", help="Password (prefer --password-file or --password-env)")
    parser.add_argument("--password-env", help="Read password from this environment variable")
    parser.add_argument("--password-file", help="Read password from this file")
    parser.add_argument(
        "--insecure", action="store_true", help="Disable TLS certificate verification"
    )


# -- generate subcommand --


def _add_generate_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "generate",
        help="Generate a Sieve script from JSON alias mappings.",
    )
    p.add_argument("config", type=Path, help="[required] JSON config file")
    p.add_argument("--output", type=Path, help="Write generated script here (default: stdout)")
    p.add_argument("--script-name", help="Override script name from config")

    upload = p.add_argument_group("ManageSieve upload (only with --upload)")
    upload.add_argument(
        "--upload", action="store_true", help="Upload via ManageSieve after generation"
    )
    upload.add_argument("--no-activate", action="store_true", help="Upload but do not activate")
    upload.add_argument("--no-check", action="store_true", help="Skip CHECKSCRIPT before upload")
    upload.add_argument("--host", help="ManageSieve host[:port] (default port: 4190)")
    upload.add_argument("--username", help="ManageSieve username")
    upload.add_argument("--authz-id", default="", help="Optional SASL authorization ID")
    upload.add_argument(
        "--tls-mode",
        choices=["auto", "starttls", "implicit", "plain"],
        default="auto",
        help="TLS mode (default: auto)",
    )
    _add_password_args(upload)
    p.set_defaults(func=_cmd_generate)


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        eprint(f"Config error: {exc}")
        return 2

    if args.script_name:
        config.script_name = args.script_name.strip()

    script_text = generate_sieve(config)
    write_output(script_text, args.output)

    eprint(f"Generated {len(config.rules)} rule(s) for script {config.script_name!r}.")
    if args.output:
        eprint(f"Wrote: {args.output}")

    if not args.upload:
        return 0

    # Prompt for missing mandatory upload args.
    host_raw = args.host or _prompt("ManageSieve host[:port]")
    host, port = _parse_host_port(host_raw, 4190)
    username = args.username or _prompt("ManageSieve username")

    try:
        scripts = upload_via_managesieve(
            host=host,
            port=port,
            username=username,
            password=resolve_password(
                args.password,
                args.password_env,
                args.password_file,
                prompt="ManageSieve password: ",
            ),
            script_name=config.script_name,
            script_text=script_text,
            tls_mode=args.tls_mode,
            insecure=args.insecure,
            authz_id=args.authz_id,
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
        default=["To", "Delivered-To", "X-Original-To"],
        help="Headers to extract aliases from (default: To Delivered-To X-Original-To)",
    )
    p.add_argument(
        "--default-folder",
        default="INBOX",
        help="Default folder in generated mapping (default: INBOX)",
    )
    p.add_argument("--output", type=Path, help="Write JSON mapping here (default: stdout)")
    p.add_argument(
        "--tls-mode",
        choices=["implicit", "starttls", "plain"],
        default="implicit",
        help="TLS mode for IMAP (default: implicit)",
    )
    _add_password_args(p)
    p.set_defaults(func=_cmd_extract_aliases)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_extract_aliases(args: argparse.Namespace) -> int:
    # Prompt for mandatory args if missing.
    server_raw = args.server or _prompt("IMAP server (host or host:port)")
    host, port = _parse_host_port(server_raw, 993)
    user = args.user or _prompt("IMAP username")
    domain = args.domain or _prompt("Domain filter (e.g. company.com)")

    # Strip leading @ from domain if user typed it.
    domain = domain.lstrip("@")

    since = _parse_date(args.since) if args.since else None

    try:
        password = resolve_password(
            args.password,
            args.password_env,
            args.password_file,
            prompt="IMAP password: ",
        )
        conn = connect_imap(
            host=host,
            port=port,
            user=user,
            password=password,
            tls_mode=args.tls_mode,
            insecure=args.insecure,
        )
    except Exception as exc:
        eprint(f"IMAP connection failed: {exc}")
        return 1

    try:
        aliases = extract_aliases(
            conn,
            folder=args.folder,
            domain=domain,
            headers=tuple(args.headers),
            limit=args.limit,
            since=since,
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
    mapping = build_alias_mapping(aliases, default_folder=args.default_folder)
    text = write_alias_mapping(mapping, args.output)
    if args.output is None:
        sys.stdout.write(text)
    else:
        eprint(f"Wrote: {args.output}")

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
