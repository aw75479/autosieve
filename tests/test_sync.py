"""Tests for the sync command (autosieve.commands.sync).

The sync command itself is a thin orchestrator: each underlying CLI command
(_cmd_extract, _cmd_generate, _cmd_apply, _cmd_upload) is independently
covered by tests/test_cli.py.  These tests focus on:

- Step skipping flags (--no-extract, --no-apply, --no-upload).
- Confirmation behaviour of --yes vs --yes-apply.
- Pipeline aborts on a step failure.
- argparse registration.
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

from autosieve.cli import main


def _basic_target_toml(tmp_path):
    """A minimal valid multi-target config used as the sync entry point."""
    aliases = tmp_path / "default" / "aliases.json"
    aliases.parent.mkdir(parents=True, exist_ok=True)
    aliases.write_text('{"rules": [{"alias": "x@y.com", "folder": "F"}]}\n')

    toml = tmp_path / "autosieve.toml"
    toml.write_text(
        f'data_dir = "{tmp_path}"\n\n'
        "[[targets]]\n"
        'name = "default"\n'
        "[targets.imap]\n"
        'host = "h"\nuser = "u"\n'
        "[targets.managesieve]\n"
        'host = "h"\nusername = "u"\n'
        "[targets.filenames]\n"
        f'sieve_file = "{tmp_path / "out.sieve"}"\n'
    )
    return toml


class TestSyncRegistered:
    def test_subcommand_appears_in_help(self, capsys):
        with contextlib.suppress(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "sync" in out


class TestSyncSkipFlags:
    def test_no_extract_skips_extract(self, tmp_path):
        toml = _basic_target_toml(tmp_path)
        with (
            patch("autosieve.cli._cmd_extract", return_value=0) as mext,
            patch("autosieve.cli._cmd_generate", return_value=0) as mgen,
            patch("autosieve.cli._cmd_apply", return_value=0) as mapp,
            patch("autosieve.cli._cmd_upload", return_value=0) as mup,
        ):
            rc = main(
                [
                    "sync",
                    "--config",
                    str(toml),
                    "--no-extract",
                    "--no-apply",
                    "--no-upload",
                    "--yes",
                ]
            )
        assert rc == 0
        mext.assert_not_called()
        mapp.assert_not_called()
        mup.assert_not_called()
        mgen.assert_called_once()

    def test_full_pipeline_with_yes_and_yes_apply(self, tmp_path):
        toml = _basic_target_toml(tmp_path)
        with (
            patch("autosieve.cli._cmd_extract", return_value=0) as mext,
            patch("autosieve.cli._cmd_generate", return_value=0) as mgen,
            patch("autosieve.cli._cmd_apply", return_value=0) as mapp,
            patch("autosieve.cli._cmd_upload", return_value=0) as mup,
        ):
            rc = main(
                [
                    "sync",
                    "--config",
                    str(toml),
                    "--yes",
                    "--yes-apply",
                ]
            )
        assert rc == 0
        for m in (mext, mgen, mapp, mup):
            m.assert_called_once()


class TestSyncFailureAborts:
    def test_failed_step_returns_its_exit_code(self, tmp_path):
        toml = _basic_target_toml(tmp_path)
        with (
            patch("autosieve.cli._cmd_extract", return_value=0),
            patch("autosieve.cli._cmd_generate", return_value=7) as mgen,
            patch("autosieve.cli._cmd_apply", return_value=0) as mapp,
            patch("autosieve.cli._cmd_upload", return_value=0) as mup,
        ):
            rc = main(
                [
                    "sync",
                    "--config",
                    str(toml),
                    "--yes",
                    "--yes-apply",
                ]
            )
        assert rc == 7
        mgen.assert_called_once()
        mapp.assert_not_called()
        mup.assert_not_called()


class TestSyncApplyConfirmation:
    def test_apply_skipped_on_no_response(self, tmp_path):
        toml = _basic_target_toml(tmp_path)
        with (
            patch("autosieve.cli._cmd_extract", return_value=0),
            patch("autosieve.cli._cmd_generate", return_value=0),
            patch("autosieve.cli._cmd_apply", return_value=0) as mapp,
            patch("autosieve.cli._cmd_upload", return_value=0) as mup,
            patch("autosieve.commands.sync._confirm", return_value=False),
        ):
            # --yes skips non-destructive prompts, but --yes-apply NOT given
            # so apply asks via _confirm() which we patched to return False.
            rc = main(
                [
                    "sync",
                    "--config",
                    str(toml),
                    "--yes",
                ]
            )
        assert rc == 0
        mapp.assert_not_called()
        mup.assert_called_once()

    def test_apply_runs_when_confirmed(self, tmp_path):
        toml = _basic_target_toml(tmp_path)
        with (
            patch("autosieve.cli._cmd_extract", return_value=0),
            patch("autosieve.cli._cmd_generate", return_value=0),
            patch("autosieve.cli._cmd_apply", return_value=0) as mapp,
            patch("autosieve.cli._cmd_upload", return_value=0),
            patch("autosieve.commands.sync._confirm", return_value=True),
        ):
            rc = main(
                [
                    "sync",
                    "--config",
                    str(toml),
                    "--yes",
                ]
            )
        assert rc == 0
        mapp.assert_called_once()
