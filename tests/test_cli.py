"""Tests for mailfilter.cli."""

from __future__ import annotations

import pytest

from mailfilter.cli import _parse_host_port, main, resolve_password


class TestParseHostPort:
    def test_host_only(self):
        assert _parse_host_port("mail.example.com", 993) == ("mail.example.com", 993)

    def test_host_and_port(self):
        assert _parse_host_port("mail.example.com:143", 993) == ("mail.example.com", 143)

    def test_non_numeric_port_uses_default(self):
        # Non-numeric port part: treated as plain hostname.
        assert _parse_host_port("mail.example.com:abc", 993) == ("mail.example.com:abc", 993)


class TestResolvePassword:
    def test_direct(self):
        assert resolve_password(password="secret") == "secret"

    def test_env(self, monkeypatch):
        monkeypatch.setenv("TEST_PW", "fromenv")
        assert resolve_password(password_env="TEST_PW") == "fromenv"

    def test_env_missing(self):
        with pytest.raises(RuntimeError, match="not set"):
            resolve_password(password_env="NONEXISTENT_VAR_12345")

    def test_file(self, tmp_path):
        f = tmp_path / "pw"
        f.write_text("frompwfile\n")
        assert resolve_password(password_file=str(f)) == "frompwfile"

    def test_priority_direct_over_env(self, monkeypatch):
        monkeypatch.setenv("TEST_PW", "fromenv")
        assert resolve_password(password="direct", password_env="TEST_PW") == "direct"


class TestCLIGenerate:
    def test_generate_stdout(self, sample_config_path, capsys):
        rc = main(["generate", str(sample_config_path)])
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
        rc = main(["generate", str(sample_config_path), "--script-name", "custom"])
        assert rc == 0


class TestCLINoCommand:
    def test_no_subcommand(self, capsys):
        rc = main([])
        assert rc == 2


class TestCLIExtractAliases:
    def test_help_doesnt_crash(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["extract-aliases", "--help"])
        assert exc_info.value.code == 0
