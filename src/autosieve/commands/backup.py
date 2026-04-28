"""``autosieve backup`` -- snapshot local alias config and (optionally) remote scripts.

This module is **independent**: delete this file to remove the ``backup``
subcommand without affecting the rest of the CLI (cli.py loads it via
importlib in ``build_arg_parser``).

Snapshot layout (under ``<target.data_dir>/backups/<ISO-timestamp>/``)::

    aliases.json          # copy of target.alias_path()
    <sieve_basename>      # copy of target.sieve_path() if it exists
    manifest.json         # snapshot metadata (target name, timestamp, contents)
    remote/               # only when --remote is given
        <script-name>     # one file per server script
        _active           # text file naming the active script (if any)

Snapshots are intentionally plain files so users can ``cp -a`` / ``rsync``
them around, and ``autosieve restore`` is a thin reverse mapping.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``backup`` subparser."""
    from autosieve.cli import _add_target_arg

    p = subparsers.add_parser(
        "backup",
        help="Snapshot the target's aliases and (optionally) server-side scripts.",
        description=(
            "Creates a timestamped snapshot under <data_dir>/backups/. "
            "By default only local files (aliases.json + local sieve file) "
            "are saved. Pass --remote to additionally connect to the "
            "ManageSieve server and download all installed scripts."
        ),
    )
    p.add_argument("--config", type=Path, help="Server config TOML file")
    _add_target_arg(p)
    p.add_argument("--list", action="store_true", help="List existing snapshots for the target and exit")
    p.add_argument("--remote", action="store_true", help="Also download all scripts from the ManageSieve server")
    p.add_argument("--output-dir", type=Path, help="Override snapshot output directory (default: <data_dir>/backups/<ts>/)")
    p.add_argument("--no-aliases", action="store_true", help="Do not copy aliases.json")
    p.add_argument("--no-local-sieve", action="store_true", help="Do not copy the local sieve file")
    # ManageSieve auth (only used with --remote)
    p.add_argument("--password", help="ManageSieve password (prompted if omitted; only with --remote)")
    p.set_defaults(func=_cmd_backup)


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _timestamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _cmd_backup(args: argparse.Namespace) -> int:
    from autosieve.cli import _resolve_target
    from autosieve.server_config import load_server_config

    if not args.config:
        _eprint("backup: --config is required.")
        return 2
    srv = load_server_config(args.config)
    target = _resolve_target(args, srv)
    if target is None:
        _eprint("backup: no target resolved (check --target / default_target).")
        return 2

    backup_root = target.data_dir(srv.data_dir) / "backups"

    if args.list:
        if not backup_root.is_dir():
            _eprint(f"(no snapshots in {backup_root})")
            return 0
        snapshots = sorted(p.name for p in backup_root.iterdir() if p.is_dir())
        for name in snapshots:
            print(name)
        return 0

    snapshot_dir = args.output_dir if args.output_dir else backup_root / _timestamp()
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "target": target.name,
        "timestamp_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "files": [],
        "remote_scripts": [],
        "active_remote_script": None,
    }

    # Local files.
    if not args.no_aliases:
        src = target.alias_path(srv.data_dir)
        if src.is_file():
            dst = snapshot_dir / "aliases.json"
            shutil.copy2(src, dst)
            manifest["files"].append("aliases.json")
            _eprint(f"backup: copied {src} -> {dst}")
        else:
            _eprint(f"backup: aliases file missing, skipped ({src})")

    if not args.no_local_sieve:
        src = target.sieve_path(srv.data_dir)
        if src.is_file():
            dst = snapshot_dir / src.name
            shutil.copy2(src, dst)
            manifest["files"].append(src.name)
            _eprint(f"backup: copied {src} -> {dst}")
        else:
            _eprint(f"backup: local sieve file missing, skipped ({src})")

    # Remote scripts.
    if args.remote:
        rc = _backup_remote(args, target, snapshot_dir, manifest)
        if rc != 0:
            return rc

    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(str(snapshot_dir))
    return 0


def _backup_remote(
    args: argparse.Namespace,
    target: Any,
    snapshot_dir: Path,
    manifest: dict[str, Any],
) -> int:
    """Download all scripts from the ManageSieve server into ``remote/``."""
    from autosieve.cli import _keyring_key, resolve_password
    from autosieve.managesieve import ManageSieveClient, ManageSieveError

    ms = target.managesieve
    if ms.host is None or ms.username is None:
        _eprint("backup: target has no ManageSieve host/username; cannot use --remote.")
        return 2

    password = resolve_password(
        args.password,
        keyring_service="autosieve",
        keyring_user=_keyring_key("managesieve", ms.username, ms.host),
        prompt=f"ManageSieve password for {ms.username}@{ms.host}: ",
    )
    if not password:
        password = getpass.getpass(f"ManageSieve password for {ms.username}@{ms.host}: ")

    remote_dir = snapshot_dir / "remote"
    remote_dir.mkdir(parents=True, exist_ok=True)

    try:
        with ManageSieveClient(
            host=ms.host,
            port=ms.port or 4190,
            connection_security=ms.connection_security,
            insecure=False,
        ) as client:
            client.connect()
            if ms.connection_security == "starttls":
                client.starttls()
            client.authenticate_plain(ms.username, password, authz_id="")
            scripts = client.list_scripts()
            for name, is_active in scripts:
                body = client.get_script(name)
                # Replace path separators in script names defensively.
                safe = name.replace("/", "_").replace("\\", "_")
                (remote_dir / safe).write_text(body)
                manifest["remote_scripts"].append({"name": name, "file": safe, "active": is_active})
                if is_active:
                    manifest["active_remote_script"] = name
                    (remote_dir / "_active").write_text(name + "\n")
                _eprint(f"backup: downloaded remote script {name!r} ({len(body)} bytes){' [active]' if is_active else ''}")
    except (ManageSieveError, OSError) as exc:
        _eprint(f"backup: remote download failed: {exc}")
        return 3
    return 0
