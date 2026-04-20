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
        toml.write_text('[imap]\nfolder = "Archive"\n')
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

    def test_legacy_alias_section(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[alias]\nfolder_prefix = "legacy"\n')
        cfg = load_server_config(toml)
        assert cfg.managesieve.folder_prefix == "legacy"

    def test_filenames_section(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[filenames]\nsieve_file = "custom.sieve"\nalias_file = "custom.json"\n')
        cfg = load_server_config(toml)
        assert cfg.filenames.sieve_file == "custom.sieve"
        assert cfg.filenames.alias_file == "custom.json"

    def test_legacy_output_section(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[output]\nsieve_file = "old.sieve"\n')
        cfg = load_server_config(toml)
        assert cfg.filenames.sieve_file == "old.sieve"
