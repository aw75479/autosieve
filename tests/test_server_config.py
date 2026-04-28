"""Tests for autosieve.server_config."""

from __future__ import annotations

from autosieve.server_config import load_server_config


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
        from autosieve.server_config import _parse_security

        assert _parse_security("ssl", "imap") == "ssl"

    def test_valid_starttls(self):
        from autosieve.server_config import _parse_security

        assert _parse_security("STARTTLS", "imap") == "starttls"

    def test_valid_none(self):
        from autosieve.server_config import _parse_security

        assert _parse_security("none", "imap") == "none"

    def test_invalid_raises(self):
        """Invalid connection_security raises ValueError (line 92 in server_config.py)."""
        import pytest

        from autosieve.server_config import _parse_security

        with pytest.raises(ValueError, match="connection_security"):
            _parse_security("tls", "imap")


class TestMultiTarget:
    """v0.2.0 [[targets]] array shape."""

    def _two_targets_toml(self):
        return (
            'default_target = "personal"\n'
            'data_dir = "./mydata"\n\n'
            "[[targets]]\n"
            'name = "personal"\n'
            "[targets.imap]\n"
            'host = "p.example.com"\nuser = "me@p.example.com"\n'
            "[targets.managesieve]\n"
            'host = "p.example.com"\nusername = "me@p.example.com"\n\n'
            "[[targets]]\n"
            'name = "work"\n'
            "[targets.imap]\n"
            'host = "w.example.com"\nuser = "me@w.example.com"\nauth = "xoauth2"\n'
            "[targets.managesieve]\n"
            'host = "w.example.com"\nusername = "me@w.example.com"\n'
        )

    def test_two_targets_loaded(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(self._two_targets_toml())
        cfg = load_server_config(toml)
        assert cfg.target_names() == ["personal", "work"]
        assert cfg.default_target == "personal"
        assert cfg.data_dir == "./mydata"

    def test_get_target_by_name(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(self._two_targets_toml())
        cfg = load_server_config(toml)
        work = cfg.get_target("work")
        assert work.imap.host == "w.example.com"
        assert work.imap.auth == "xoauth2"

    def test_get_target_default(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(self._two_targets_toml())
        cfg = load_server_config(toml)
        default = cfg.get_target()
        assert default.name == "personal"

    def test_get_target_unknown_raises(self, tmp_path):
        import pytest

        from autosieve.server_config import ConfigSchemaError

        toml = tmp_path / "cfg.toml"
        toml.write_text(self._two_targets_toml())
        cfg = load_server_config(toml)
        with pytest.raises(ConfigSchemaError, match="unknown target"):
            cfg.get_target("nonexistent")

    def test_legacy_shim_uses_default_target(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(self._two_targets_toml())
        cfg = load_server_config(toml)
        # The legacy cfg.imap shim returns the default target (personal).
        assert cfg.imap.host == "p.example.com"

    def test_per_target_data_dir_override(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[[targets]]\nname = "x"\ndata_dir = "/abs/custom"\n[targets.imap]\nhost = "h"\n[targets.managesieve]\nhost = "h"\n')
        cfg = load_server_config(toml)
        t = cfg.get_target("x")
        assert t.data_dir() == __import__("pathlib").Path("/abs/custom")

    def test_default_data_dir_layout(self, tmp_path):
        from pathlib import Path

        toml = tmp_path / "cfg.toml"
        toml.write_text('[[targets]]\nname = "alpha"\n[targets.imap]\nhost = "h"\n[targets.managesieve]\nhost = "h"\n')
        cfg = load_server_config(toml)
        t = cfg.get_target("alpha")
        # Default data_dir is "./targets"; per-target folder is <data_dir>/<name>.
        assert t.data_dir(cfg.data_dir) == Path("./targets/alpha").expanduser()

    def test_alias_and_sieve_paths_relative_to_target_dir(self, tmp_path):
        from pathlib import Path

        toml = tmp_path / "cfg.toml"
        toml.write_text(
            "[[targets]]\n"
            'name = "alpha"\n'
            '[targets.imap]\nhost = "h"\n'
            '[targets.managesieve]\nhost = "h"\n'
            '[targets.filenames]\nalias_file = "a.json"\nsieve_file = "s.sieve"\n'
        )
        cfg = load_server_config(toml)
        t = cfg.get_target("alpha")
        assert t.alias_path(cfg.data_dir) == Path("./targets/alpha/a.json").expanduser()
        assert t.sieve_path(cfg.data_dir) == Path("./targets/alpha/s.sieve").expanduser()

    def test_absolute_alias_file_kept_absolute(self, tmp_path):
        from pathlib import Path

        toml = tmp_path / "cfg.toml"
        toml.write_text(
            "[[targets]]\n"
            'name = "alpha"\n'
            '[targets.imap]\nhost = "h"\n'
            '[targets.managesieve]\nhost = "h"\n'
            '[targets.filenames]\nalias_file = "/etc/aliases.json"\n'
        )
        cfg = load_server_config(toml)
        t = cfg.get_target("alpha")
        assert t.alias_path(cfg.data_dir) == Path("/etc/aliases.json")

    def test_unknown_subtable_preserved_as_feature_block(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text(
            '[[targets]]\nname = "x"\n[targets.imap]\nhost = "h"\n[targets.managesieve]\nhost = "h"\n[targets.vacation]\nenabled = true\nsubject = "Out"\n'
        )
        cfg = load_server_config(toml)
        t = cfg.get_target("x")
        block = t.feature_block("vacation")
        assert block == {"enabled": True, "subject": "Out"}
        assert t.feature_block("notify") is None

    def test_duplicate_target_names_rejected(self, tmp_path):
        import pytest

        from autosieve.server_config import ConfigSchemaError

        toml = tmp_path / "cfg.toml"
        toml.write_text(
            '[[targets]]\nname = "x"\n[targets.imap]\nhost="h"\n[targets.managesieve]\nhost="h"\n'
            '[[targets]]\nname = "x"\n[targets.imap]\nhost="h"\n[targets.managesieve]\nhost="h"\n'
        )
        with pytest.raises(ConfigSchemaError, match="duplicate target"):
            load_server_config(toml)

    def test_mixed_top_level_and_targets_rejected(self, tmp_path):
        import pytest

        from autosieve.server_config import ConfigSchemaError

        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nhost = "x"\n[[targets]]\nname="y"\n[targets.imap]\nhost="z"\n[targets.managesieve]\nhost="z"\n')
        with pytest.raises(ConfigSchemaError, match="both"):
            load_server_config(toml)

    def test_invalid_auth_rejected(self, tmp_path):
        import pytest

        from autosieve.server_config import ConfigSchemaError

        toml = tmp_path / "cfg.toml"
        toml.write_text('[imap]\nauth = "kerberos"\n')
        with pytest.raises(ConfigSchemaError, match="auth must be"):
            load_server_config(toml)

    def test_managesieve_scripts_default_empty(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text("[managesieve]\n")
        cfg = load_server_config(toml)
        assert cfg.managesieve.scripts == []

    def test_managesieve_scripts_list(self, tmp_path):
        toml = tmp_path / "cfg.toml"
        toml.write_text('[managesieve]\nscripts = ["aliases", "vacation"]\n')
        cfg = load_server_config(toml)
        assert cfg.managesieve.scripts == ["aliases", "vacation"]

    def test_single_target_implicit_default(self, tmp_path):
        # When only one target is declared and default_target isn't set,
        # the loader uses that target's name as the default.
        toml = tmp_path / "cfg.toml"
        toml.write_text('[[targets]]\nname="only"\n[targets.imap]\nhost="h"\n[targets.managesieve]\nhost="h"\n')
        cfg = load_server_config(toml)
        assert cfg.default_target == "only"
        assert cfg.get_target().name == "only"
