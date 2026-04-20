"""Tests for mailfilter.config."""

from __future__ import annotations

import json

import pytest

from mailfilter.config import Config, ConfigError, Rule, _normalize_rules, load_config


class TestNormalizeRules:
    def test_dict_format(self):
        raw = {"alice@example.com": "Clients/Alice", "bob@example.com": "Clients/Bob"}
        rules = _normalize_rules(raw)
        assert len(rules) == 2
        assert rules[0] == Rule(aliases=["alice@example.com"], folder="Clients/Alice")

    def test_list_with_alias_string(self):
        raw = [{"alias": "a@b.com", "folder": "F"}]
        rules = _normalize_rules(raw)
        assert rules[0].aliases == ["a@b.com"]

    def test_list_with_aliases_list(self):
        raw = [{"aliases": ["a@b.com", "c@b.com"], "folder": "F"}]
        rules = _normalize_rules(raw)
        assert rules[0].aliases == ["a@b.com", "c@b.com"]

    def test_alias_and_aliases_merged(self):
        raw = [{"alias": "a@b.com", "aliases": ["c@b.com"], "folder": "F"}]
        rules = _normalize_rules(raw)
        assert rules[0].aliases == ["a@b.com", "c@b.com"]

    def test_missing_folder_raises(self):
        with pytest.raises(ConfigError, match="non-empty string 'folder'"):
            _normalize_rules([{"alias": "a@b.com"}])

    def test_missing_alias_raises(self):
        with pytest.raises(ConfigError, match="needs 'alias' or 'aliases'"):
            _normalize_rules([{"folder": "F"}])

    def test_invalid_type_raises(self):
        with pytest.raises(ConfigError):
            _normalize_rules("not a list or dict")

    def test_comment_preserved(self):
        raw = [{"alias": "a@b.com", "folder": "F", "comment": "Test comment"}]
        rules = _normalize_rules(raw)
        assert rules[0].comment == "Test comment"

    def test_whitespace_stripped(self):
        raw = [{"alias": "  a@b.com  ", "folder": "  F  "}]
        rules = _normalize_rules(raw)
        assert rules[0].aliases == ["a@b.com"]
        assert rules[0].folder == "F"


class TestLoadConfig:
    def test_load_sample(self, sample_config_path):
        config = load_config(sample_config_path)
        assert isinstance(config, Config)
        assert config.script_name == "alias-router"
        assert config.use_create is True
        assert config.match_type == "is"
        assert len(config.rules) == 2

    def test_defaults(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}]}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.headers == ["X-Original-To", "Delivered-To"]
        assert config.use_create is False
        assert config.explicit_keep is False
        assert config.match_type == "is"
        assert config.script_name == "alias-router"

    def test_invalid_match_type(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "match_type": "regex"}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ConfigError, match="match_type"):
            load_config(p)

    def test_missing_rules(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("{}")
        with pytest.raises(ConfigError, match="missing 'rules'"):
            load_config(p)

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            load_config(p)

    def test_contains_match_type(self, tmp_path):
        data = {
            "rules": [{"alias": "a@b.com", "folder": "F"}],
            "match_type": "contains",
        }
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.match_type == "contains"
