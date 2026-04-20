"""Tests for mailfilter.server_config."""

from __future__ import annotations

from mailfilter.server_config import load_server_config


class TestLoadServerConfig:
    def test_full(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(
            '[imap]\nhost = "mx.test"\nport = 143\nuser = "u"\n'
            'domain = "test.com"\n\n'
            '[managesieve]\nhost = "mx.test"\nport = 4190\nusername = "u"\n\n'
            '[alias]\nfolder_prefix = "clients"\n'
        )
        cfg = load_server_config(toml)
        assert cfg.imap.host == "mx.test"
        assert cfg.imap.port == 143
        assert cfg.imap.domain == "test.com"
        assert cfg.managesieve.host == "mx.test"
        assert cfg.alias.folder_prefix == "clients"

    def test_defaults(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("")
        cfg = load_server_config(toml)
        assert cfg.imap.host == ""
        assert cfg.imap.port == 993
        assert cfg.managesieve.port == 4190
        assert cfg.alias.folder_prefix == "alias"

    def test_partial(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nhost = "mail.co"\n')
        cfg = load_server_config(toml)
        assert cfg.imap.host == "mail.co"
        assert cfg.managesieve.host == ""
