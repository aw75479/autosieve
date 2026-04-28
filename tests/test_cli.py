"""Tests for autosieve.cli."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from autosieve.cli import _keyring_key, _parse_host_port, main, resolve_password


class TestParseHostPort:
    def test_host_only(self):
        assert _parse_host_port("mail.example.com", 993) == ("mail.example.com", 993)

    def test_host_and_port(self):
        assert _parse_host_port("mail.example.com:143", 993) == ("mail.example.com", 143)

    def test_non_numeric_port_uses_default(self):
        # Non-numeric port part: treated as plain hostname.
        assert _parse_host_port("mail.example.com:abc", 993) == ("mail.example.com:abc", 993)


class TestKeyringKey:
    def test_format(self):
        assert _keyring_key("imap", "user@host", "mail.co") == "imap://user@host@mail.co"


class TestResolvePassword:
    def test_direct(self):
        assert resolve_password(password="secret") == "secret"

    def test_prompt_fallback(self, monkeypatch):
        monkeypatch.setattr("autosieve.cli.getpass.getpass", lambda prompt: "prompted")
        assert resolve_password() == "prompted"

    def test_keyring_lookup(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "from-keyring"
        monkeypatch.setattr("autosieve.cli._keyring", mock_kr)
        pw = resolve_password(keyring_service="svc", keyring_user="usr")
        assert pw == "from-keyring"
        mock_kr.get_password.assert_called_once_with("svc", "usr")

    def test_keyring_miss_falls_through(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr("autosieve.cli._keyring", mock_kr)
        monkeypatch.setattr("autosieve.cli.getpass.getpass", lambda prompt: "manual")
        pw = resolve_password(keyring_service="svc", keyring_user="usr")
        assert pw == "manual"

    def test_store_password_in_keyring(self, monkeypatch):
        mock_kr = MagicMock()
        monkeypatch.setattr("autosieve.cli._keyring", mock_kr)
        resolve_password(password="direct", keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        mock_kr.set_password.assert_called_once_with("svc", "usr", "direct")

    def test_store_prompted_password(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr("autosieve.cli._keyring", mock_kr)
        monkeypatch.setattr("autosieve.cli.getpass.getpass", lambda prompt: "typed")
        resolve_password(keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        mock_kr.set_password.assert_called_once_with("svc", "usr", "typed")

    def test_no_keyring_available(self, monkeypatch):
        monkeypatch.setattr("autosieve.cli._keyring", None)
        monkeypatch.setattr("autosieve.cli.getpass.getpass", lambda prompt: "fallback")
        pw = resolve_password(keyring_service="svc", keyring_user="usr")
        assert pw == "fallback"


class TestCLIGenerate:
    def test_generate_stdout(self, sample_config_path, capsys):
        rc = main(["generate", str(sample_config_path), "--stdout"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "fileinto" in out
        assert "kunde1@maildomain.de" in out

    def test_generate_to_file(self, sample_config_path, tmp_path):
        out_file = tmp_path / "out.sieve"
        rc = main(["generate", str(sample_config_path), "--output", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "fileinto" in content

    def test_generate_bad_config(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        rc = main(["generate", str(bad)])
        assert rc == 2

    def test_generate_override_script_name(self, sample_config_path, capsys):
        rc = main(["generate", str(sample_config_path), "--script-name", "custom", "--stdout"])
        assert rc == 0

    def test_generate_dry_run_no_existing(self, sample_config_path, tmp_path, capsys):
        nonexistent = tmp_path / "out.sieve"
        rc = main(["generate", str(sample_config_path), "--dry-run", "--output", str(nonexistent)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "fileinto" in out

    def test_generate_dry_run_no_changes(self, sample_config_path, tmp_path, capsys):
        from autosieve.config import load_alias_config
        from autosieve.sieve import generate_sieve

        config = load_alias_config(sample_config_path)
        existing = tmp_path / "out.sieve"
        existing.write_text(generate_sieve(config), encoding="utf-8", newline="\n")
        rc = main(["generate", str(sample_config_path), "--dry-run", "--output", str(existing)])
        assert rc == 0
        err = capsys.readouterr().err
        assert "No changes" in err

    def test_generate_dry_run_with_diff(self, sample_config_path, tmp_path, capsys):
        existing = tmp_path / "out.sieve"
        existing.write_text("# old content\n")
        rc = main(["generate", str(sample_config_path), "--dry-run", "--output", str(existing)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "---" in out  # unified diff header

    def test_generate_with_toml_config(self, sample_config_path, tmp_path, capsys):
        toml = tmp_path / "autosieve.toml"
        toml.write_text('[filenames]\nsieve_file = "custom.sieve"\n')
        rc = main(["generate", str(sample_config_path), "--config", str(toml), "--stdout"])
        assert rc == 0

    def test_generate_bad_toml_config(self, sample_config_path, tmp_path, capsys):
        bad_toml = tmp_path / "bad.toml"
        bad_toml.write_text("invalid toml {{{{")
        rc = main(["generate", str(sample_config_path), "--config", str(bad_toml)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error" in err

    def test_inactive_rules_message(self, tmp_path, capsys):
        data = {
            "rules": [
                {"alias": "a@b.com", "folder": "F"},
                {"alias": "c@b.com", "folder": "G", "active": False},
            ]
        }
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        rc = main(["generate", str(p), "--stdout"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "1 active" in err
        assert "1 inactive" in err

    def test_generate_to_stdout_no_file(self, sample_config_path, capsys):
        rc = main(["generate", str(sample_config_path), "--stdout"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "fileinto" in out

    def test_generate_alias_file_defaults_to_config(self, tmp_path, capsys):
        """When alias_file is omitted, the path from filenames.alias_file is used."""
        alias_file = tmp_path / "my.json"
        alias_file.write_text(
            json.dumps(
                {
                    "rules": [{"alias": "a@b.com", "folder": "F"}],
                    "headers": ["To"],
                }
            )
        )
        toml = tmp_path / "autosieve.toml"
        toml.write_text(f'[filenames]\nalias_file = "{alias_file}"\nsieve_file = "/dev/null"\n')
        rc = main(["generate", "--config", str(toml), "--stdout"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "fileinto" in out

    def test_generate_writes_fallback_for_mismatched_rules(self, tmp_path, capsys):
        """Envelope mode: mismatched rules are included as header rules in the combined output."""
        alias_file = tmp_path / "aliases.json"
        alias_file.write_text(
            json.dumps(
                {
                    "generation_mode": "envelope",
                    "folder_prefix": "alias",
                    "folder_sep": ".",
                    "catch_all_folder": "alias._other",
                    "rules": [
                        {"alias": "alice@example.com", "folder": "alias.alice"},
                        {"alias": "typo@example.com", "folder": "alias.typos"},
                    ],
                    "headers": ["To"],
                }
            )
        )
        sieve_out = tmp_path / "autosieve.sieve"
        rc = main(["generate", str(alias_file), "--output", str(sieve_out)])
        assert rc == 0
        combined_text = sieve_out.read_text()
        # Mismatched rule is included as a header rule in the combined file.
        assert "typo@example.com" in combined_text
        # Envelope-compatible rule is expressed as a compact address block.
        assert '"alice"' in combined_text
        # No separate custom file is written.
        custom_path = tmp_path / "autosieve-custom.sieve"
        assert not custom_path.exists()

    def test_generate_no_fallback_when_all_rules_match(self, tmp_path, capsys):
        """When all rules fit the envelope pattern no extra file is written."""
        alias_file = tmp_path / "aliases.json"
        alias_file.write_text(
            json.dumps(
                {
                    "generation_mode": "envelope",
                    "folder_prefix": "alias",
                    "folder_sep": ".",
                    "catch_all_folder": "alias._other",
                    "rules": [{"alias": "alice@example.com", "folder": "alias.alice"}],
                    "headers": ["To"],
                }
            )
        )
        sieve_out = tmp_path / "autosieve.sieve"
        rc = main(["generate", str(alias_file), "--output", str(sieve_out)])
        assert rc == 0
        # No separate custom file is written.
        custom_path = tmp_path / "autosieve-custom.sieve"
        assert not custom_path.exists()


class TestCLINoCommand:
    def test_no_subcommand(self, capsys):
        rc = main([])
        assert rc == 2


class TestCLIUpload:
    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_success(self, mock_pw, mock_upload, sample_config_path, capsys):
        mock_upload.return_value = [("autosieve", True)]
        rc = main(
            [
                "generate",
                str(sample_config_path),
                "--upload",
                "--host",
                "mail.test",
                "--username",
                "user",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "ManageSieve upload complete" in err

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_failure(self, mock_pw, mock_upload, sample_config_path, capsys):
        mock_upload.side_effect = Exception("connection refused")
        rc = main(
            [
                "generate",
                str(sample_config_path),
                "--upload",
                "--host",
                "mail.test",
                "--username",
                "user",
                "--password",
                "pw",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Upload failed" in err

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_no_activate(self, mock_pw, mock_upload, sample_config_path, capsys):
        mock_upload.return_value = [("autosieve", False)]
        rc = main(
            [
                "generate",
                str(sample_config_path),
                "--upload",
                "--host",
                "mail.test",
                "--username",
                "user",
                "--password",
                "pw",
                "--no-check",
                "--no-activate",
            ]
        )
        assert rc == 0

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_with_toml_config(self, mock_pw, mock_upload, sample_config_path, tmp_path, capsys):
        toml = tmp_path / "autosieve.toml"
        toml.write_text('[managesieve]\nhost = "ms.test"\nport = 4190\nusername = "u"\npassword = "p"\n')
        mock_upload.return_value = []
        rc = main(["generate", str(sample_config_path), "--upload", "--config", str(toml)])
        assert rc == 0


class TestCLIExtract:
    def test_help_doesnt_crash(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["extract", "--help"])
        assert exc_info.value.code == 0

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_basic_extract(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_imap = MagicMock()
        mock_conn.return_value = mock_imap
        mock_extract.return_value = {"alice@example.com": {"To"}}
        out_file = tmp_path / "aliases.json"
        rc = main(
            [
                "extract",
                "mail.test",
                str(out_file),
                "--user",
                "u",
                "--domain",
                "example.com",
                "--password",
                "pw",
                "--folder",
                "INBOX",
            ]
        )
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "rules" in data
        assert data["generation_mode"] == "envelope"

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_no_aliases(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {}
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "example.com",
                "--password",
                "pw",
                "--stdout",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "No aliases found" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_reports_no_new_aliases(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        existing = tmp_path / "aliases.json"
        existing.write_text(
            json.dumps(
                {
                    "generation_mode": "envelope",
                    "rules": [{"alias": "a@test.com", "folder": "alias/a"}],
                }
            )
        )
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(
            [
                "extract",
                "mail.test",
                str(existing),
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "No new aliases to add" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_verbose_lists_aliases(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}, "b@test.com": set()}
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--verbose",
                "--stdout",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "found: a@test.com [To]" in err
        assert "found: b@test.com [(received-only)]" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_then_generate_envelope_e2e(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {
            "aicamp@example.com": {"To"},
            "aws@example.com": {"To"},
            "apple@example.com": {"To"},
        }
        alias_file = tmp_path / "aliases.json"
        rc_extract = main(
            [
                "extract",
                "mail.test",
                str(alias_file),
                "--user",
                "u",
                "--domain",
                "example.com",
                "--password",
                "pw",
            ]
        )
        assert rc_extract == 0

        rc_generate = main(["generate", str(alias_file), "--stdout"])
        assert rc_generate == 0
        out = capsys.readouterr().out
        assert 'require ["fileinto", "variables"];' in out
        assert 'address :domain :is "To" "example.com"' in out
        assert '"aicamp"' in out
        assert '"apple"' in out
        assert '"aws"' in out
        assert 'fileinto "alias.${alias}";' in out
        assert 'fileinto "alias._other";' in out

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_multi_folder(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.side_effect = [
            {"a@test.com": {"To"}},
            {"b@test.com": {"Delivered-To"}},
        ]
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--folder",
                "INBOX",
                "Sent",
                "--stdout",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "a@test.com" in out
        assert "b@test.com" in out

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_dry_run(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--dry-run",
                "--stdout",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "a@test.com" in out

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_dry_run_no_changes(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}

        # Write initial file.
        existing = tmp_path / "aliases.json"
        rc1 = main(
            [
                "extract",
                "mail.test",
                str(existing),
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--folder",
                "INBOX",
            ]
        )
        assert rc1 == 0

        # Dry-run should show "No changes" since same alias already there.
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc2 = main(
            [
                "extract",
                "mail.test",
                str(existing),
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--dry-run",
            ]
        )
        assert rc2 == 0

    @patch("autosieve.cli.connect_imap")
    def test_extract_connection_failure(self, mock_conn, capsys):
        mock_conn.side_effect = Exception("timeout")
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--stdout",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "IMAP connection failed" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_failure(self, mock_extract, mock_conn, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.side_effect = Exception("IMAP error")
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--stdout",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Extraction failed" in err

    def test_extract_bad_toml_config(self, tmp_path, capsys):
        bad_toml = tmp_path / "bad.toml"
        bad_toml.write_text("invalid toml {{{{")
        rc = main(["extract", "--config", str(bad_toml), "--stdout"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_dry_run_with_diff(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        # First call: create an alias file.
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        rc1 = main(["extract", "mail.test", str(existing), "--user", "u", "--domain", "co.com", "--password", "pw"])
        assert rc1 == 0
        # Second call: different aliases, dry-run should show diff.
        mock_extract.return_value = {"b@co.com": {"To"}}
        rc2 = main(["extract", "mail.test", str(existing), "--user", "u", "--domain", "co.com", "--password", "pw", "--dry-run"])
        assert rc2 == 0
        out = capsys.readouterr().out
        assert "---" in out  # unified diff header

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_with_toml_config(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        toml = tmp_path / "autosieve.toml"
        toml.write_text('[imap]\nhost = "mail.co"\nuser = "u"\ndomain = "co.com"\npassword = "pw"\n')
        rc = main(["extract", "--config", str(toml), "--stdout"])
        assert rc == 0

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_incremental(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        existing.write_text(json.dumps({"rules": [], "last_fetched": "2025-01-01"}))
        rc = main(
            [
                "extract",
                "mail.test",
                str(existing),
                "--user",
                "u",
                "--domain",
                "co.com",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "Incremental" in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_no_incremental(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        existing.write_text(json.dumps({"rules": [], "last_fetched": "2025-01-01"}))
        rc = main(
            [
                "extract",
                "mail.test",
                str(existing),
                "--user",
                "u",
                "--domain",
                "co.com",
                "--password",
                "pw",
                "--no-incremental",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "Incremental" not in err

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_since(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "co.com",
                "--password",
                "pw",
                "--since",
                "2025-06-01",
                "--stdout",
            ]
        )
        assert rc == 0


class TestPromptAndParseDate:
    def test_prompt(self, monkeypatch):
        from autosieve.cli import _prompt

        monkeypatch.setattr("sys.stdin", MagicMock())
        monkeypatch.setattr("builtins.input", lambda: "answer")
        result = _prompt("Question")
        assert result == "answer"

    def test_parse_date(self):
        from autosieve.cli import _parse_date

        result = _parse_date("2025-06-15")
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15


class TestCLIUploadCommand:
    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_script_success(self, mock_pw, mock_upload, tmp_path, capsys):
        script = tmp_path / "autosieve.sieve"
        script.write_text('require ["fileinto"];\n')
        mock_upload.return_value = [("autosieve", True)]
        rc = main(["upload", str(script), "--host", "mail.test", "--username", "u", "--password", "pw"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "ManageSieve upload complete" in err

    def test_upload_missing_script(self, tmp_path, capsys):
        missing = tmp_path / "missing.sieve"
        rc = main(["upload", str(missing), "--host", "mail.test", "--username", "u", "--password", "pw"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Script file not found" in err


class TestCLIApplyCommand:
    @patch("autosieve.cli.apply_rules_imap", return_value={"alias.alice": 3})
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_dry_run(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
                "--dry-run",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "Would move" in err

    @patch("autosieve.cli.apply_rules_imap", return_value={})
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_no_matches(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "Moved 0 message" in err

    def test_apply_bad_alias_file(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        rc = main(["apply", str(bad), "--host", "mail.test", "--user", "u", "--password", "pw"])
        assert rc == 2


class TestCLIMissingLines:
    """Tests targeting previously uncovered CLI lines."""

    # -- resolve_password keyring warnings (lines 126, 145) --

    def test_store_password_no_keyring_direct(self, monkeypatch, capsys):
        """store_in_keyring=True but keyring unavailable warns when password provided directly (line 126)."""
        monkeypatch.setattr("autosieve.cli._keyring", None)
        from autosieve.cli import resolve_password

        pw = resolve_password(password="direct", keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        assert pw == "direct"
        err = capsys.readouterr().err
        assert "keyring package not available" in err

    def test_store_password_no_keyring_via_getpass(self, monkeypatch, capsys):
        """store_in_keyring=True but keyring unavailable warns when password comes from getpass (line 145)."""
        monkeypatch.setattr("autosieve.cli._keyring", None)
        monkeypatch.setattr("autosieve.cli.getpass.getpass", lambda prompt: "typed")
        from autosieve.cli import resolve_password

        pw = resolve_password(keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        assert pw == "typed"
        err = capsys.readouterr().err
        assert "keyring package not available" in err

    # -- generate command default output path (line 226) --

    def test_generate_default_output_no_stdout_no_config(self, tmp_path, monkeypatch, sample_config_path, capsys):
        """When no --stdout/--output/config is given, output goes to DEFAULT_SIEVE_FILE (line 226)."""
        monkeypatch.chdir(tmp_path)
        rc = main(["generate", str(sample_config_path)])
        assert rc == 0
        assert (tmp_path / "aliasfilter.sieve").exists()

    # -- _upload_script connection_security='none' warning (line 276) --

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_none_security_warns(self, mock_pw, mock_upload, sample_config_path, capsys):
        """generate --upload with --connection-security none prints warning (line 276)."""
        mock_upload.return_value = []
        rc = main(
            [
                "generate",
                str(sample_config_path),
                "--upload",
                "--host",
                "mail.test",
                "--username",
                "u",
                "--password",
                "pw",
                "--connection-security",
                "none",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "credentials will be transmitted unencrypted" in err

    # -- extract command connection_security='none' warning (line 373) --

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_none_security_warns(self, mock_extract, mock_conn, tmp_path, capsys):
        """extract with --connection-security none prints warning (line 373)."""
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(
            [
                "extract",
                "mail.test",
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
                "--connection-security",
                "none",
                "--stdout",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "credentials will be transmitted unencrypted" in err

    # -- extract default output path via srv (line 387) --

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_output_from_srv_config(self, mock_extract, mock_conn, tmp_path, capsys):
        """extract without --alias-file uses srv.filenames.alias_file (line 387)."""
        alias_path = tmp_path / "from_config.json"
        toml = tmp_path / "autosieve.toml"
        toml.write_text(f'[imap]\nhost = "mail.test"\nuser = "u"\ndomain = "test.com"\npassword = "pw"\n[filenames]\nalias_file = "{alias_path}"\n')
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(["extract", "--config", str(toml)])
        assert rc == 0
        assert alias_path.exists()

    # -- extract existing alias file read error (lines 399-400) --

    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.extract_aliases")
    def test_extract_corrupt_existing_alias_file_warns(self, mock_extract, mock_conn, tmp_path, capsys):
        """extract with a corrupt existing alias file warns and continues (lines 399-400)."""
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{ INVALID JSON }")
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(
            [
                "extract",
                "mail.test",
                str(corrupt),
                "--user",
                "u",
                "--domain",
                "test.com",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "could not read existing alias file" in err

    # -- upload subcommand bad toml (lines 525-527) --

    def test_upload_bad_toml_config(self, tmp_path, capsys):
        """upload subcommand with a bad TOML config returns rc=2 (lines 525-527)."""
        script = tmp_path / "s.sieve"
        script.write_text('require ["fileinto"];\n')
        bad_toml = tmp_path / "bad.toml"
        bad_toml.write_text("invalid = {{{")
        rc = main(["upload", str(script), "--config", str(bad_toml), "--host", "h", "--username", "u"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error" in err

    # -- upload subcommand script_path from srv (lines 531-532) --

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_script_path_from_srv(self, mock_pw, mock_upload, tmp_path, capsys):
        """upload uses srv.filenames.sieve_file when no positional arg given (lines 531-532)."""
        script = tmp_path / "srv.sieve"
        script.write_text('require ["fileinto"];\n')
        toml = tmp_path / "autosieve.toml"
        toml.write_text(f'[filenames]\nsieve_file = "{script}"\n[managesieve]\nhost = "ms.test"\nusername = "u"\npassword = "pw"\n')
        mock_upload.return_value = [("srv", True)]
        rc = main(["upload", "--config", str(toml)])
        assert rc == 0

    # -- upload subcommand default sieve file path (line 534) --

    @patch("autosieve.cli.upload_via_managesieve")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_upload_default_script_path(self, mock_pw, mock_upload, tmp_path, monkeypatch, capsys):
        """upload with no positional arg and no srv falls back to DEFAULT_SIEVE_FILE (line 534)."""
        monkeypatch.chdir(tmp_path)
        default_sieve = tmp_path / "aliasfilter.sieve"
        default_sieve.write_text('require ["fileinto"];\n')
        mock_upload.return_value = []
        rc = main(["upload", "--host", "h", "--username", "u", "--password", "pw"])
        assert rc == 0

    # -- upload subcommand script read error (lines 542-544) --

    def test_upload_script_read_error(self, tmp_path, capsys):
        """upload with unreadable script file returns rc=2 (lines 542-544)."""
        script = tmp_path / "unreadable.sieve"
        script.write_text("content")
        script.chmod(0o000)
        try:
            rc = main(["upload", str(script), "--host", "h", "--username", "u", "--password", "pw"])
            assert rc == 2
            err = capsys.readouterr().err
            assert "Script file error" in err
        finally:
            script.chmod(0o644)

    # -- apply subcommand bad toml (lines 582-584) --

    def test_apply_bad_toml_config(self, tmp_path, capsys):
        """apply with bad TOML returns rc=2 (lines 582-584)."""
        alias = tmp_path / "a.json"
        alias.write_text('{"rules": []}')
        bad_toml = tmp_path / "bad.toml"
        bad_toml.write_text("invalid = {{{")
        rc = main(["apply", str(alias), "--config", str(bad_toml), "--host", "h", "--user", "u"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error" in err

    # -- apply subcommand connection_security='none' warning (line 601) --

    @patch("autosieve.cli.apply_rules_imap", return_value={})
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_none_security_warns(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        """apply with --connection-security none prints warning (line 601)."""
        mock_conn.return_value = MagicMock()
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
                "--connection-security",
                "none",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "credentials will be transmitted unencrypted" in err

    # -- apply IMAP connection failure (lines 616-618) --

    @patch("autosieve.cli.connect_imap")
    def test_apply_imap_connection_failure(self, mock_conn, sample_config_path, capsys):
        """apply with IMAP connection failure returns rc=1 (lines 616-618)."""
        mock_conn.side_effect = Exception("connection refused")
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "IMAP connection failed" in err

    # -- apply progress callback with count > 0 (lines 625-627) --

    @patch("autosieve.cli.apply_rules_imap")
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_progress_reports_moves(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        """apply prints per-folder move counts via progress callback (lines 625-627)."""
        mock_conn.return_value = MagicMock()
        mock_apply.return_value = {"Korrespondenten/Kunde1": 5}

        # Capture the progress callback passed to apply_rules_imap and invoke it.
        def fake_apply(conn, config, folders, **kwargs):
            progress = kwargs.get("progress")
            if progress:
                progress("Korrespondenten/Kunde1", 5)
            return {"Korrespondenten/Kunde1": 5}

        mock_apply.side_effect = fake_apply
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "moved 5" in err or "Moved" in err

    # -- apply_rules_imap raises exception (lines 638-640) --

    @patch("autosieve.cli.apply_rules_imap")
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_exception_returns_1(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        """apply_rules_imap raising an exception returns rc=1 (lines 638-640)."""
        mock_conn.return_value = MagicMock()
        mock_apply.side_effect = Exception("unexpected IMAP error")
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Apply failed" in err

    @patch("autosieve.cli.apply_rules_imap")
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_new_folders_hint(self, mock_pw, mock_conn, mock_apply, sample_config_path, capsys):
        """apply prints a mail-client refresh hint when new folders are created."""
        mock_conn.return_value = MagicMock()

        def fake_apply(conn, config, folders, **kwargs):
            folder_created = kwargs.get("folder_created")
            if folder_created:
                folder_created("alias.newone")
            return {"alias.newone": 2}

        mock_apply.side_effect = fake_apply
        rc = main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "alias.newone" in err
        assert "refresh" in err.lower()

    @patch("autosieve.cli.apply_rules_imap")
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_subscribe_default_on(self, mock_pw, mock_conn, mock_apply, sample_config_path):
        """--subscribe is on by default: subscribe_folders=True is passed to apply_rules_imap."""
        mock_conn.return_value = MagicMock()
        mock_apply.return_value = {}
        main(["apply", str(sample_config_path), "--host", "mail.test", "--user", "u", "--password", "pw"])
        _, kwargs = mock_apply.call_args
        assert kwargs.get("subscribe_folders") is True

    @patch("autosieve.cli.apply_rules_imap")
    @patch("autosieve.cli.connect_imap")
    @patch("autosieve.cli.resolve_password", return_value="pw")
    def test_apply_no_subscribe_flag(self, mock_pw, mock_conn, mock_apply, sample_config_path):
        """--no-subscribe turns off subscribe_folders."""
        mock_conn.return_value = MagicMock()
        mock_apply.return_value = {}
        main(
            [
                "apply",
                str(sample_config_path),
                "--host",
                "mail.test",
                "--user",
                "u",
                "--password",
                "pw",
                "--no-subscribe",
            ]
        )
        _, kwargs = mock_apply.call_args
        assert kwargs.get("subscribe_folders") is False
