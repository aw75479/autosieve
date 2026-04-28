"""``autosieve restore`` -- restore a snapshot created by ``autosieve backup``.

Independent feature module (delete to disable).  See backup.py for the
on-disk layout.

By design, restore is interactive: it lists what will change and asks for
confirmation before touching local files; the destructive ``--remote``
re-upload step requires a separate ``--yes-remote`` flag so an accidental
``restore`` cannot silently overwrite live server scripts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``restore`` subparser."""
    from autosieve.cli import _add_target_arg

    p = subparsers.add_parser(
        "restore",
        help="Restore a snapshot created by 'autosieve backup'.",
        description=(
            "Restore local alias and sieve files from a snapshot directory. Pass --remote to also re-upload server-side scripts (extra confirmation required)."
        ),
    )
    p.add_argument("--config", type=Path, help="Server config TOML file")
    _add_target_arg(p)
    p.add_argument("--snapshot", help="Snapshot timestamp (default: latest); also accepts a full path")
    p.add_argument("--list", action="store_true", help="List available snapshots and exit")
    p.add_argument("--remote", action="store_true", help="Also re-upload remote scripts saved in the snapshot (destructive)")
    p.add_argument("-y", "--yes", action="store_true", help="Do not prompt for local file overwrite")
    p.add_argument("--yes-remote", action="store_true", help="Do not prompt before re-uploading server scripts (use with care)")
    p.add_argument("--password", help="ManageSieve password (only with --remote)")
    p.set_defaults(func=_cmd_restore)


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _confirm(prompt: str, default_yes: bool = False) -> bool:
    suffix = " [Y/n] " if default_yes else " [y/N] "
    sys.stderr.write(prompt + suffix)
    sys.stderr.flush()
    try:
        ans = input().strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in {"y", "yes"}


def _resolve_snapshot_dir(target: Any, base_data_dir: str, snapshot: str | None) -> Path | None:
    """Locate the snapshot directory, accepting either a name or a full path."""
    if snapshot:
        candidate = Path(snapshot)
        if candidate.is_dir():
            return candidate
        candidate = target.data_dir(base_data_dir) / "backups" / snapshot
        if candidate.is_dir():
            return candidate
        return None
    backups = target.data_dir(base_data_dir) / "backups"
    if not backups.is_dir():
        return None
    snapshots = sorted(p for p in backups.iterdir() if p.is_dir())
    return snapshots[-1] if snapshots else None


def _cmd_restore(args: argparse.Namespace) -> int:
    from autosieve.cli import _resolve_target
    from autosieve.server_config import load_server_config

    if not args.config:
        _eprint("restore: --config is required.")
        return 2
    srv = load_server_config(args.config)
    target = _resolve_target(args, srv)
    if target is None:
        _eprint("restore: no target resolved (check --target / default_target).")
        return 2

    if args.list:
        backups = target.data_dir(srv.data_dir) / "backups"
        if not backups.is_dir():
            _eprint(f"(no snapshots in {backups})")
            return 0
        for name in sorted(p.name for p in backups.iterdir() if p.is_dir()):
            print(name)
        return 0

    snap_dir = _resolve_snapshot_dir(target, srv.data_dir, args.snapshot)
    if snap_dir is None:
        _eprint("restore: no snapshot found (use --list to see available snapshots).")
        return 2

    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.is_file():
        _eprint(f"restore: missing manifest.json in {snap_dir}.")
        return 2
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())

    _eprint(f"restore: using snapshot {snap_dir}")
    _eprint(f"  target: {manifest.get('target')!r} (current: {target.name!r})")
    _eprint(f"  taken:  {manifest.get('timestamp_utc')}")
    _eprint(f"  files:  {manifest.get('files', [])}")
    if manifest.get("remote_scripts"):
        _eprint(f"  remote: {len(manifest['remote_scripts'])} script(s) (active: {manifest.get('active_remote_script')!r})")

    if not args.yes and not _confirm("restore: overwrite local files now?", default_yes=False):
        _eprint("restore: cancelled.")
        return 0

    # Local restore.
    aliases_src = snap_dir / "aliases.json"
    if aliases_src.is_file():
        dst = target.alias_path(srv.data_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(aliases_src, dst)
        _eprint(f"restore: wrote {dst}")
    sieve_dst = target.sieve_path(srv.data_dir)
    sieve_src = snap_dir / sieve_dst.name
    if sieve_src.is_file():
        sieve_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sieve_src, sieve_dst)
        _eprint(f"restore: wrote {sieve_dst}")

    if args.remote:
        rc = _restore_remote(args, target, snap_dir, manifest)
        if rc != 0:
            return rc

    _eprint("restore: complete.")
    return 0


def _restore_remote(
    args: argparse.Namespace,
    target: Any,
    snap_dir: Path,
    manifest: dict[str, Any],
) -> int:
    from autosieve.cli import _keyring_key, resolve_password
    from autosieve.managesieve import ManageSieveClient, ManageSieveError

    remote_dir = snap_dir / "remote"
    if not remote_dir.is_dir():
        _eprint("restore: snapshot has no remote/ directory; nothing to upload.")
        return 0

    scripts: list[dict[str, Any]] = list(manifest.get("remote_scripts") or [])
    if not scripts:
        _eprint("restore: manifest lists no remote scripts; nothing to upload.")
        return 0

    if not args.yes_remote and not _confirm(
        f"restore: re-upload {len(scripts)} script(s) to ManageSieve server (overwrites)?",
        default_yes=False,
    ):
        _eprint("restore: remote upload cancelled.")
        return 0

    ms = target.managesieve
    if ms.host is None or ms.username is None:
        _eprint("restore: target has no ManageSieve host/username; cannot use --remote.")
        return 2
    password = resolve_password(
        args.password,
        keyring_service="autosieve",
        keyring_user=_keyring_key("managesieve", ms.username, ms.host),
        prompt=f"ManageSieve password for {ms.username}@{ms.host}: ",
    )

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
            for entry in scripts:
                name = entry["name"]
                body_path = remote_dir / entry["file"]
                if not body_path.is_file():
                    _eprint(f"restore: skipping {name!r}, body file missing ({body_path}).")
                    continue
                body = body_path.read_text()
                client.put_script(name, body)
                _eprint(f"restore: uploaded {name!r} ({len(body)} bytes)")
            active = manifest.get("active_remote_script")
            if active:
                client.set_active(active)
                _eprint(f"restore: activated {active!r}")
    except (ManageSieveError, OSError) as exc:
        _eprint(f"restore: remote upload failed: {exc}")
        return 3
    return 0
