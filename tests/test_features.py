"""Tests for autosieve.features modules.

Each feature module is independent; these tests verify:
- emit_sieve returns None when the feature is disabled / unconfigured.
- merge_features correctly augments the require statement and appends blocks.
- vacation, notify, custom_filters all produce expected Sieve fragments.
- oauth2 token_command strategy works and surfaces useful errors.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autosieve.features import merge_features
from autosieve.features.custom_filters import emit_sieve as emit_custom
from autosieve.features.notify import emit_sieve as emit_notify
from autosieve.features.oauth2 import OAuth2Error, build_xoauth2_sasl, get_xoauth2_token
from autosieve.features.vacation import emit_sieve as emit_vacation


def _target(features: dict) -> SimpleNamespace:
    """Build a fake Target whose feature_block() returns the given dict."""
    return SimpleNamespace(
        name="default",
        feature_block=lambda name: features.get(name) or {},
    )


class TestMergeFeatures:
    def test_no_changes_when_no_blocks_or_caps(self):
        text = 'require ["fileinto"];\nkeep;\n'
        assert merge_features(text, [], []) == text

    def test_adds_capability_to_list_form(self):
        text = 'require ["fileinto"];\nkeep;\n'
        out = merge_features(text, [], ["vacation"])
        assert 'require ["fileinto", "vacation"];' in out

    def test_adds_capability_to_single_form(self):
        text = 'require "fileinto";\nkeep;\n'
        out = merge_features(text, [], ["vacation"])
        assert 'require ["fileinto", "vacation"];' in out

    def test_does_not_duplicate_capabilities(self):
        text = 'require ["fileinto", "vacation"];\nkeep;\n'
        out = merge_features(text, [], ["vacation", "fileinto"])
        # No new require line generated; just ensure caps weren't doubled.
        assert out.count("vacation") == 1
        assert out.count("fileinto") == 1

    def test_inserts_require_when_missing(self):
        text = "keep;\n"
        out = merge_features(text, ["# block"], ["vacation"])
        assert out.startswith('require ["vacation"];\n')

    def test_appends_blocks_after_main_script(self):
        text = 'require ["fileinto"];\nkeep;\n'
        out = merge_features(text, ["vacation :days 5;"], ["vacation"])
        assert "vacation :days 5;" in out
        assert out.index("keep;") < out.index("vacation :days 5;")


class TestVacation:
    def test_disabled_returns_none(self):
        assert emit_vacation(_target({}), None) is None
        assert emit_vacation(_target({"vacation": {"enabled": False}}), None) is None

    def test_enabled_with_inline_body(self):
        block, caps = emit_vacation(
            _target({"vacation": {"enabled": True, "body": "Away.", "subject": "OOO", "days": 3}}),
            None,
        )
        assert "vacation" in caps
        assert ":days 3" in block
        assert ':subject "OOO"' in block
        assert "Away." in block

    def test_enabled_with_body_file(self, tmp_path: Path):
        f = tmp_path / "msg.txt"
        f.write_text("From a file.")
        block, _ = emit_vacation(
            _target({"vacation": {"enabled": True, "body_file": str(f)}}),
            None,
        )
        assert "From a file." in block

    def test_enabled_but_no_body_emits_warning_comment(self):
        block, caps = emit_vacation(_target({"vacation": {"enabled": True}}), None)
        assert "skipped" in block
        assert caps == set()


class TestNotify:
    def test_disabled_returns_none(self):
        assert emit_notify(_target({}), None) is None

    def test_emits_rule(self):
        block, caps = emit_notify(
            _target(
                {
                    "notify": {
                        "enabled": True,
                        "rules": [
                            {
                                "name": "boss",
                                "if_from": "boss@example.com",
                                "method": "mailto:phone@sms.example.com",
                                "message": "URGENT",
                            }
                        ],
                    }
                }
            ),
            None,
        )
        assert "enotify" in caps
        assert "notify :method" in block
        assert "boss@example.com" in block

    def test_rule_without_method_is_skipped(self):
        result = emit_notify(
            _target({"notify": {"enabled": True, "rules": [{"name": "x", "if_from": "a@b"}]}}),
            None,
        )
        assert result is None  # only header comment -> dropped


class TestCustomFilters:
    def test_disabled_returns_none(self):
        assert emit_custom(_target({}), None) is None

    def test_fileinto_rule(self):
        block, caps = emit_custom(
            _target(
                {
                    "custom_filters": {
                        "enabled": True,
                        "rules": [
                            {
                                "name": "newsletters",
                                "if_from": "*@news.example.com",
                                "action": "fileinto",
                                "folder": "INBOX/News",
                            }
                        ],
                    }
                }
            ),
            None,
        )
        assert "fileinto" in caps
        assert ':matches "From"' in block  # glob -> :matches
        assert 'fileinto "INBOX/News";' in block

    def test_discard_with_subject_contains(self):
        block, caps = emit_custom(
            _target(
                {
                    "custom_filters": {
                        "enabled": True,
                        "rules": [{"name": "spam", "if_subject": "WINNER", "action": "discard"}],
                    }
                }
            ),
            None,
        )
        assert "discard;" in block
        assert ':contains "Subject"' in block
        assert "fileinto" not in caps

    def test_body_test_adds_body_capability(self):
        _, caps = emit_custom(
            _target(
                {
                    "custom_filters": {
                        "enabled": True,
                        "rules": [{"name": "b", "if_body": "secret", "action": "discard"}],
                    }
                }
            ),
            None,
        )
        assert "body" in caps

    def test_unknown_action_dropped(self):
        result = emit_custom(
            _target(
                {
                    "custom_filters": {
                        "enabled": True,
                        "rules": [{"name": "x", "if_from": "a@b", "action": "warp-drive"}],
                    }
                }
            ),
            None,
        )
        assert result is None


class TestOauth2:
    def test_no_block_raises(self):
        with pytest.raises(OAuth2Error, match=r"no .*oauth2.* block"):
            get_xoauth2_token(_target({}))

    def test_token_command_returns_stdout(self, tmp_path: Path):
        script = tmp_path / "tok.sh"
        script.write_text("#!/usr/bin/env sh\nprintf 'fake-token-xyz'\n")
        script.chmod(0o755)
        token = get_xoauth2_token(_target({"oauth2": {"token_command": str(script)}}))
        assert token == "fake-token-xyz"  # noqa: S105 - test fixture, not a real secret

    def test_token_command_failure(self, tmp_path: Path):
        script = tmp_path / "fail.sh"
        script.write_text("#!/usr/bin/env sh\nexit 7\n")
        script.chmod(0o755)
        with pytest.raises(OAuth2Error, match="exit 7"):
            get_xoauth2_token(_target({"oauth2": {"token_command": str(script)}}))

    def test_token_command_empty_output(self, tmp_path: Path):
        script = tmp_path / "empty.sh"
        script.write_text("#!/usr/bin/env sh\nexit 0\n")
        script.chmod(0o755)
        with pytest.raises(OAuth2Error, match="empty"):
            get_xoauth2_token(_target({"oauth2": {"token_command": str(script)}}))

    def test_provider_scaffolded_only(self):
        with pytest.raises(OAuth2Error, match="not yet implemented"):
            get_xoauth2_token(_target({"oauth2": {"provider": "gmail"}}))

    def test_build_xoauth2_sasl(self):
        s = build_xoauth2_sasl("a@b.com", "TKN")
        assert s == "user=a@b.com\x01auth=Bearer TKN\x01\x01"


class TestRuleTags:
    def test_tag_field_round_trip(self, tmp_path: Path):
        from autosieve.config import load_alias_config

        f = tmp_path / "a.json"
        f.write_text('{"rules": [{"alias": "x@y.com", "folder": "F", "tags": ["work", "urgent"]}]}\n')
        cfg = load_alias_config(f)
        assert cfg.rules[0].tags == ["work", "urgent"]

    def test_default_tags_is_empty(self, tmp_path: Path):
        from autosieve.config import load_alias_config

        f = tmp_path / "a.json"
        f.write_text('{"rules": [{"alias": "x@y.com", "folder": "F"}]}\n')
        cfg = load_alias_config(f)
        assert cfg.rules[0].tags == []
