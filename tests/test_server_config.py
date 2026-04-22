"""Tests for mailfilter.server_config."""

from __future__ import annotations

from mailfilter.server_config import load_server_config


class TestLoadServerConfig:
    def test_full(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(
            '[imap]\nhost = "mx.test"\nport = 143\nuser = "u"\n'
            'domain = "test.com"\n\n'
            '[managesieve]\nhost = "mx.test"\nport = 4190\nusername = "u"\n'
            'folder_prefix = "clients"\n'
        )
        cfg = load_server_config(toml)
        assert cfg.imap.host == "mx.test"
        assert cfg.imap.port == 143
        assert cfg.imap.domain == "test.com"
        assert cfg.managesieve.host == "mx.test"
        assert cfg.managesieve.folder_prefix == "clients"

    def test_defaults(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("")
        cfg = load_server_config(toml)
        assert cfg.imap.host == ""
        assert cfg.imap.port == 993
        assert cfg.imap.connection_security == "ssl"
        assert cfg.managesieve.port == 4190
        assert cfg.managesieve.folder_prefix == "alias"

    def test_partial(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nhost = "mail.co"\n')
        cfg = load_server_config(toml)
        assert cfg.imap.host == "mail.co"
        assert cfg.managesieve.host == ""

    def test_folders_list(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nfolders = ["INBOX", "Sent"]\n')
        cfg = load_server_config(toml)
        assert cfg.imap.folders == ["INBOX", "Sent"]

    def test_folder_string_converted_to_list(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nfolders = "Archive"\n')
        cfg = load_server_config(toml)
        assert cfg.imap.folders == ["Archive"]

    def test_incremental_default_true(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("")
        cfg = load_server_config(toml)
        assert cfg.imap.incremental is True

    def test_incremental_disabled(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("[imap]\nincremental = false\n")
        cfg = load_server_config(toml)
        assert cfg.imap.incremental is False

    def test_folder_prefix_in_managesieve(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[managesieve]\nfolder_prefix = "clients"\n')
        cfg = load_server_config(toml)
        assert cfg.managesieve.folder_prefix == "clients"

    def test_legacy_alias_section_ignored(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[alias]\nfolder_prefix = "legacy"\n')
        cfg = load_server_config(toml)
        assert cfg.managesieve.folder_prefix == "alias"

    def test_filenames_section(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[filenames]\nsieve_file = "custom.sieve"\nalias_file = "custom.json"\n')
        cfg = load_server_config(toml)
        assert cfg.filenames.sieve_file == "custom.sieve"
        assert cfg.filenames.alias_file == "custom.json"

    def test_legacy_output_section_ignored(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[output]\nsieve_file = "old.sieve"\n')
        cfg = load_server_config(toml)
        assert cfg.filenames.sieve_file == "aliasfilter.sieve"

    def test_store_password_flags(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("[imap]\nstore_password = true\n\n[managesieve]\nstore_password = true\n")
        cfg = load_server_config(toml)
        assert cfg.imap.store_password is True
        assert cfg.managesieve.store_password is True

    def test_use_imap_password_default_false(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("")
        cfg = load_server_config(toml)
        assert cfg.managesieve.use_imap_password is False

    def test_use_imap_password_enabled(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("[managesieve]\nuse_imap_password = true\n")
        cfg = load_server_config(toml)
        assert cfg.managesieve.use_imap_password is True

    def test_folder_sep_default_dot(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("")
        cfg = load_server_config(toml)
        assert cfg.imap.folder_sep == "."
        assert cfg.managesieve.folder_sep == "."

    def test_folder_sep_configurable(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nfolder_sep = "/"\n\n[managesieve]\nfolder_sep = "/"\n')
        cfg = load_server_config(toml)
        assert cfg.imap.folder_sep == "/"
        assert cfg.managesieve.folder_sep == "/"


class TestParseSecurityHelper:
    def test_valid_ssl(self):
        from mailfilter.server_config import _parse_security

        assert _parse_security("ssl", "imap") == "ssl"

    def test_valid_starttls(self):
        from mailfilter.server_config import _parse_security

        assert _parse_security("STARTTLS", "imap") == "starttls"

    def test_valid_none(self):
        from mailfilter.server_config import _parse_security

        assert _parse_security("none", "imap") == "none"

    def test_invalid_raises(self):
        """Invalid connection_security raises ValueError (line 92 in server_config.py)."""
        import pytest

        from mailfilter.server_config import _parse_security

        with pytest.raises(ValueError, match="connection_security"):
            _parse_security("tls", "imap")
