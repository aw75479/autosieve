"""Tests for mailfilter.cli."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mailfilter.cli import _keyring_key, _parse_host_port, main, resolve_password


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
        monkeypatch.setattr("mailfilter.cli.getpass.getpass", lambda prompt: "prompted")
        assert resolve_password() == "prompted"

    def test_keyring_lookup(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "from-keyring"
        monkeypatch.setattr("mailfilter.cli._keyring", mock_kr)
        pw = resolve_password(keyring_service="svc", keyring_user="usr")
        assert pw == "from-keyring"
        mock_kr.get_password.assert_called_once_with("svc", "usr")

    def test_keyring_miss_falls_through(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr("mailfilter.cli._keyring", mock_kr)
        monkeypatch.setattr("mailfilter.cli.getpass.getpass", lambda prompt: "manual")
        pw = resolve_password(keyring_service="svc", keyring_user="usr")
        assert pw == "manual"

    def test_store_password_in_keyring(self, monkeypatch):
        mock_kr = MagicMock()
        monkeypatch.setattr("mailfilter.cli._keyring", mock_kr)
        resolve_password(password="direct", keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        mock_kr.set_password.assert_called_once_with("svc", "usr", "direct")

    def test_store_prompted_password(self, monkeypatch):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        monkeypatch.setattr("mailfilter.cli._keyring", mock_kr)
        monkeypatch.setattr("mailfilter.cli.getpass.getpass", lambda prompt: "typed")
        resolve_password(keyring_service="svc", keyring_user="usr", store_in_keyring=True)
        mock_kr.set_password.assert_called_once_with("svc", "usr", "typed")

    def test_no_keyring_available(self, monkeypatch):
        monkeypatch.setattr("mailfilter.cli._keyring", None)
        monkeypatch.setattr("mailfilter.cli.getpass.getpass", lambda prompt: "fallback")
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
        from mailfilter.config import load_config
        from mailfilter.sieve import generate_sieve

        config = load_config(sample_config_path)
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
        toml = tmp_path / "mailfilter.toml"
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


class TestCLINoCommand:
    def test_no_subcommand(self, capsys):
        rc = main([])
        assert rc == 2


class TestCLIUpload:
    @patch("mailfilter.cli.upload_via_managesieve")
    @patch("mailfilter.cli.resolve_password", return_value="pw")
    def test_upload_success(self, mock_pw, mock_upload, sample_config_path, capsys):
        mock_upload.return_value = [("mailfilter", True)]
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

    @patch("mailfilter.cli.upload_via_managesieve")
    @patch("mailfilter.cli.resolve_password", return_value="pw")
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

    @patch("mailfilter.cli.upload_via_managesieve")
    @patch("mailfilter.cli.resolve_password", return_value="pw")
    def test_upload_no_activate(self, mock_pw, mock_upload, sample_config_path, capsys):
        mock_upload.return_value = [("mailfilter", False)]
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

    @patch("mailfilter.cli.upload_via_managesieve")
    @patch("mailfilter.cli.resolve_password", return_value="pw")
    def test_upload_with_toml_config(self, mock_pw, mock_upload, sample_config_path, tmp_path, capsys):
        toml = tmp_path / "mailfilter.toml"
        toml.write_text('[managesieve]\nhost = "ms.test"\nport = 4190\nusername = "u"\npassword = "p"\n')
        mock_upload.return_value = []
        rc = main(["generate", str(sample_config_path), "--upload", "--config", str(toml)])
        assert rc == 0


class TestCLIExtractAliases:
    def test_help_doesnt_crash(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["extract-aliases", "--help"])
        assert exc_info.value.code == 0

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_basic_extract(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_imap = MagicMock()
        mock_conn.return_value = mock_imap
        mock_extract.return_value = {"alice@example.com": {"To"}}
        out_file = tmp_path / "aliases.json"
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_no_aliases(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {}
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_multi_folder(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.side_effect = [
            {"a@test.com": {"To"}},
            {"b@test.com": {"Delivered-To"}},
        ]
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_dry_run(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_dry_run_no_changes(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@test.com": {"To"}}

        # Write initial file.
        existing = tmp_path / "aliases.json"
        rc1 = main(
            [
                "extract-aliases",
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
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    def test_extract_connection_failure(self, mock_conn, capsys):
        mock_conn.side_effect = Exception("timeout")
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_failure(self, mock_extract, mock_conn, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.side_effect = Exception("IMAP error")
        rc = main(
            [
                "extract-aliases",
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
        rc = main(["extract-aliases", "--config", str(bad_toml), "--stdout"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error" in err

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_dry_run_with_diff(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        # First call: create an alias file.
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        rc1 = main(["extract-aliases", "mail.test", str(existing), "--user", "u", "--domain", "co.com", "--password", "pw"])
        assert rc1 == 0
        # Second call: different aliases, dry-run should show diff.
        mock_extract.return_value = {"b@co.com": {"To"}}
        rc2 = main(["extract-aliases", "mail.test", str(existing), "--user", "u", "--domain", "co.com", "--password", "pw", "--dry-run"])
        assert rc2 == 0
        out = capsys.readouterr().out
        assert "---" in out  # unified diff header

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_with_toml_config(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        toml = tmp_path / "mailfilter.toml"
        toml.write_text('[imap]\nhost = "mail.co"\nuser = "u"\ndomain = "co.com"\npassword = "pw"\n')
        rc = main(["extract-aliases", "--config", str(toml), "--stdout"])
        assert rc == 0

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_incremental(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        existing.write_text(json.dumps({"rules": [], "last_fetched": "2025-01-01"}))
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_no_incremental(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        existing = tmp_path / "aliases.json"
        existing.write_text(json.dumps({"rules": [], "last_fetched": "2025-01-01"}))
        rc = main(
            [
                "extract-aliases",
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

    @patch("mailfilter.cli.connect_imap")
    @patch("mailfilter.cli.extract_aliases")
    def test_extract_since(self, mock_extract, mock_conn, tmp_path, capsys):
        mock_conn.return_value = MagicMock()
        mock_extract.return_value = {"a@co.com": {"To"}}
        rc = main(
            [
                "extract-aliases",
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
        from mailfilter.cli import _prompt

        monkeypatch.setattr("sys.stdin", MagicMock())
        monkeypatch.setattr("builtins.input", lambda: "answer")
        result = _prompt("Question")
        assert result == "answer"

    def test_parse_date(self):
        from mailfilter.cli import _parse_date

        result = _parse_date("2025-06-15")
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
