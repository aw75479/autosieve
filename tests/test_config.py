"""Tests for mailfilter.config."""

from __future__ import annotations

import json

import pytest

from mailfilter.config import (
    Config,
    ConfigError,
    Rule,
    _merge_rules_by_folder,
    _normalize_rules,
    load_config,
)


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
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "match_type": "invalid"}
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


class TestMergeRulesByFolder:
    def test_no_duplicates(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F1"),
            Rule(aliases=["c@b.com"], folder="F2"),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 2
        assert result[0].aliases == ["a@b.com"]
        assert result[1].aliases == ["c@b.com"]

    def test_same_folder_merged(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F"),
            Rule(aliases=["c@b.com"], folder="F"),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 1
        assert result[0].folder == "F"
        assert result[0].aliases == ["a@b.com", "c@b.com"]

    def test_three_rules_same_folder(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F"),
            Rule(aliases=["c@b.com"], folder="F"),
            Rule(aliases=["d@b.com"], folder="F"),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 1
        assert result[0].aliases == ["a@b.com", "c@b.com", "d@b.com"]

    def test_mixed_folders(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F1"),
            Rule(aliases=["c@b.com"], folder="F2"),
            Rule(aliases=["d@b.com"], folder="F1"),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 2
        assert result[0].folder == "F1"
        assert result[0].aliases == ["a@b.com", "d@b.com"]
        assert result[1].folder == "F2"

    def test_duplicate_alias_deduped(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F"),
            Rule(aliases=["a@b.com", "c@b.com"], folder="F"),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 1
        assert result[0].aliases == ["a@b.com", "c@b.com"]

    def test_first_comment_wins(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F", comment="first"),
            Rule(aliases=["c@b.com"], folder="F", comment="second"),
        ]
        result = _merge_rules_by_folder(rules)
        assert result[0].comment == "first"

    def test_none_comment_inherits(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F"),
            Rule(aliases=["c@b.com"], folder="F", comment="late"),
        ]
        result = _merge_rules_by_folder(rules)
        assert result[0].comment == "late"

    def test_preserves_order(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F2"),
            Rule(aliases=["b@b.com"], folder="F1"),
            Rule(aliases=["c@b.com"], folder="F3"),
        ]
        result = _merge_rules_by_folder(rules)
        assert [r.folder for r in result] == ["F2", "F1", "F3"]

    def test_empty_input(self):
        assert _merge_rules_by_folder([]) == []

    def test_does_not_mutate_input(self):
        r1 = Rule(aliases=["a@b.com"], folder="F")
        r2 = Rule(aliases=["c@b.com"], folder="F")
        _merge_rules_by_folder([r1, r2])
        assert r1.aliases == ["a@b.com"]

    def test_normalize_rules_merges_list_format(self):
        raw = [
            {"alias": "a@b.com", "folder": "F"},
            {"alias": "c@b.com", "folder": "F"},
        ]
        rules = _normalize_rules(raw)
        assert len(rules) == 1
        assert set(rules[0].aliases) == {"a@b.com", "c@b.com"}

    def test_normalize_rules_merges_dict_format(self):
        raw = {"a@b.com": "F", "c@b.com": "F"}
        rules = _normalize_rules(raw)
        assert len(rules) == 1
        assert set(rules[0].aliases) == {"a@b.com", "c@b.com"}

    def test_load_config_merges(self, tmp_path):
        data = {
            "rules": [
                {"alias": "a@b.com", "folder": "Work"},
                {"alias": "c@b.com", "folder": "Work"},
            ]
        }
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert len(config.rules) == 1
        assert config.rules[0].folder == "Work"
        assert set(config.rules[0].aliases) == {"a@b.com", "c@b.com"}

    def test_merge_headers(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F", headers=["To"]),
            Rule(aliases=["c@b.com"], folder="F", headers=["Delivered-To"]),
        ]
        result = _merge_rules_by_folder(rules)
        assert len(result) == 1
        assert set(result[0].headers) == {"To", "Delivered-To"}

    def test_none_headers_inherits(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F"),
            Rule(aliases=["c@b.com"], folder="F", headers=["To"]),
        ]
        result = _merge_rules_by_folder(rules)
        assert result[0].headers == ["To"]

    def test_per_rule_headers_from_json(self, tmp_path):
        data = {
            "rules": [
                {"alias": "a@b.com", "folder": "F", "headers": ["X-Original-To"]},
            ]
        }
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.rules[0].headers == ["X-Original-To"]


class TestActiveField:
    def test_active_default_true(self):
        raw = [{"alias": "a@b.com", "folder": "F"}]
        rules = _normalize_rules(raw)
        assert rules[0].active is True

    def test_active_false_from_json(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F", "active": False}]}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.rules[0].active is False

    def test_merge_inactive_propagates(self):
        rules = [
            Rule(aliases=["a@b.com"], folder="F", active=True),
            Rule(aliases=["c@b.com"], folder="F", active=False),
        ]
        result = _merge_rules_by_folder(rules)
        assert result[0].active is False

    def test_invalid_active_ignored(self):
        raw = [{"alias": "a@b.com", "folder": "F", "active": "nope"}]
        rules = _normalize_rules(raw)
        assert rules[0].active is True


class TestRegexMatchType:
    def test_regex_match_type(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "match_type": "regex"}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.match_type == "regex"

    def test_matches_match_type(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "match_type": "matches"}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        config = load_config(p)
        assert config.match_type == "matches"


class TestEdgeCases:
    def test_empty_aliases_list_raises(self):
        raw = [{"aliases": [], "folder": "F"}]
        with pytest.raises(ConfigError, match="needs 'alias' or 'aliases'"):
            _normalize_rules(raw)

    def test_whitespace_only_alias_raises(self):
        raw = [{"alias": "   ", "folder": "F"}]
        with pytest.raises(ConfigError, match="needs 'alias' or 'aliases'"):
            _normalize_rules(raw)

    def test_invalid_aliases_type_raises(self):
        raw = [{"aliases": "not-a-list", "folder": "F"}]
        with pytest.raises(ConfigError, match="list of strings"):
            _normalize_rules(raw)

    def test_non_object_rule_raises(self):
        with pytest.raises(ConfigError, match="must be an object"):
            _normalize_rules(["just-a-string"])

    def test_top_level_not_object_raises(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("[]")
        with pytest.raises(ConfigError, match="top-level"):
            load_config(p)

    def test_invalid_headers_raises(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "headers": []}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ConfigError, match="headers"):
            load_config(p)

    def test_empty_script_name_raises(self, tmp_path):
        data = {"rules": [{"alias": "a@b.com", "folder": "F"}], "script_name": ""}
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ConfigError, match="script_name"):
            load_config(p)

    def test_dict_rule_non_string_raises(self):
        with pytest.raises(ConfigError, match="string alias"):
            _normalize_rules({123: "F"})

    def test_per_rule_headers_whitespace_only(self):
        raw = [{"alias": "a@b.com", "folder": "F", "headers": ["  ", ""]}]
        rules = _normalize_rules(raw)
        assert rules[0].headers is None
