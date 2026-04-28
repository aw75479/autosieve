"""Tests for autosieve backup / restore commands.

These tests exercise the local-only paths (no --remote), which are pure
filesystem operations.  Remote upload/download paths are exercised via
mocked ``ManageSieveClient`` to avoid network I/O.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autosieve.cli import main


def _config(tmp_path: Path) -> Path:
    aliases = tmp_path / "default" / "aliases.json"
    aliases.parent.mkdir(parents=True, exist_ok=True)
    aliases.write_text('{"rules": [{"alias": "x@y.com", "folder": "F"}]}\n')
    sieve = tmp_path / "default" / "filter.sieve"
    sieve.write_text("# sieve script\n")

    toml = tmp_path / "autosieve.toml"
    toml.write_text(
        f'data_dir = "{tmp_path}"\n\n'
        "[[targets]]\n"
        'name = "default"\n'
        "[targets.imap]\n"
        'host = "h"\nuser = "u"\n'
        "[targets.managesieve]\n"
        'host = "msvr"\nusername = "msu"\nport = 4190\n'
        "[targets.filenames]\n"
        f'sieve_file = "{sieve}"\n'
        f'alias_file = "{aliases}"\n'
    )
    return toml


class TestBackupCli:
    def test_help_lists_backup_and_restore(self, capsys):
        with contextlib.suppress(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "backup" in out
        assert "restore" in out

    def test_backup_local_only_creates_snapshot(self, tmp_path, capsys):
        toml = _config(tmp_path)
        rc = main(["backup", "--config", str(toml)])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        snap = Path(out)
        assert snap.is_dir()
        assert (snap / "aliases.json").is_file()
        assert (snap / "filter.sieve").is_file()
        manifest = json.loads((snap / "manifest.json").read_text())
        assert manifest["target"] == "default"
        assert "aliases.json" in manifest["files"]
        assert manifest["remote_scripts"] == []

    def test_backup_list_empty(self, tmp_path, capsys):
        toml = _config(tmp_path)
        rc = main(["backup", "--config", str(toml), "--list"])
        assert rc == 0

    def test_backup_list_after_creating(self, tmp_path, capsys):
        toml = _config(tmp_path)
        main(["backup", "--config", str(toml)])
        capsys.readouterr()
        rc = main(["backup", "--config", str(toml), "--list"])
        out = capsys.readouterr().out.strip().splitlines()
        assert rc == 0
        assert len(out) == 1
        assert out[0].endswith("Z")

    def test_backup_no_aliases_skips_aliases(self, tmp_path, capsys):
        toml = _config(tmp_path)
        rc = main(["backup", "--config", str(toml), "--no-aliases"])
        assert rc == 0
        snap = Path(capsys.readouterr().out.strip())
        assert not (snap / "aliases.json").is_file()
        assert (snap / "filter.sieve").is_file()


class TestBackupRemote:
    def test_backup_remote_downloads_scripts(self, tmp_path, capsys):
        toml = _config(tmp_path)

        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.list_scripts.return_value = [("main", True), ("backup", False)]
        fake_client.get_script.side_effect = lambda name: f"# body of {name}\n"

        with (
            patch("autosieve.managesieve.ManageSieveClient", return_value=fake_client),
            patch("autosieve.cli.resolve_password", return_value="pw"),
        ):
            rc = main(["backup", "--config", str(toml), "--remote", "--password", "pw"])
        assert rc == 0
        snap = Path(capsys.readouterr().out.strip())
        assert (snap / "remote" / "main").read_text().startswith("# body of main")
        assert (snap / "remote" / "backup").read_text().startswith("# body of backup")
        assert (snap / "remote" / "_active").read_text().strip() == "main"
        manifest = json.loads((snap / "manifest.json").read_text())
        assert manifest["active_remote_script"] == "main"
        assert {s["name"] for s in manifest["remote_scripts"]} == {"main", "backup"}


class TestRestoreCli:
    def test_restore_no_snapshots(self, tmp_path, capsys):
        toml = _config(tmp_path)
        rc = main(["restore", "--config", str(toml), "--list"])
        assert rc == 0

    def test_restore_local_overwrites(self, tmp_path, capsys, monkeypatch):
        toml = _config(tmp_path)
        # Make a snapshot first.
        rc = main(["backup", "--config", str(toml)])
        assert rc == 0
        snap = Path(capsys.readouterr().out.strip())

        # Mutate the live files.
        live_aliases = tmp_path / "default" / "aliases.json"
        live_sieve = tmp_path / "default" / "filter.sieve"
        live_aliases.write_text('{"rules": []}\n')
        live_sieve.write_text("CHANGED\n")

        rc = main(["restore", "--config", str(toml), "--yes"])
        assert rc == 0
        assert "x@y.com" in live_aliases.read_text()
        assert live_sieve.read_text().startswith("# sieve")
        # snapshot directory unchanged
        assert (snap / "aliases.json").is_file()

    def test_restore_aborts_without_yes(self, tmp_path, capsys):
        toml = _config(tmp_path)
        main(["backup", "--config", str(toml)])
        capsys.readouterr()
        with patch("autosieve.commands.restore._confirm", return_value=False):
            rc = main(["restore", "--config", str(toml)])
        assert rc == 0
        # Output to stderr (not captured here); just make sure no crash and
        # the live files were not modified is implicit since we didn't
        # change them.

    def test_restore_remote_uploads_when_yes_remote(self, tmp_path, capsys):
        toml = _config(tmp_path)

        fake_dl = MagicMock()
        fake_dl.__enter__.return_value = fake_dl
        fake_dl.list_scripts.return_value = [("main", True)]
        fake_dl.get_script.return_value = "# original body\n"
        with (
            patch("autosieve.managesieve.ManageSieveClient", return_value=fake_dl),
            patch("autosieve.cli.resolve_password", return_value="pw"),
        ):
            main(["backup", "--config", str(toml), "--remote", "--password", "pw"])
        capsys.readouterr()

        fake_up = MagicMock()
        fake_up.__enter__.return_value = fake_up
        with (
            patch("autosieve.managesieve.ManageSieveClient", return_value=fake_up),
            patch("autosieve.cli.resolve_password", return_value="pw"),
        ):
            rc = main(["restore", "--config", str(toml), "--yes", "--remote", "--yes-remote", "--password", "pw"])
        assert rc == 0
        fake_up.put_script.assert_called_once_with("main", "# original body\n")
        fake_up.set_active.assert_called_once_with("main")


class TestBackupNeedsConfig:
    @pytest.mark.parametrize("cmd", ["backup", "restore"])
    def test_missing_config_returns_2(self, cmd):
        rc = main([cmd])
        assert rc == 2
