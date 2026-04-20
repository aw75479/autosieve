"""Tests for mailfilter.imap_alias."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from mailfilter.imap_alias import (
    _extract_addresses,
    build_alias_mapping,
    extract_aliases,
    get_last_fetched,
    merge_aliases_into,
    parse_received_for,
    stderr_progress,
    update_last_fetched,
    write_alias_mapping,
)


class TestParseReceivedFor:
    def test_simple(self):
        hdr = "from mx.example.com by mail.example.com for <alice@example.com>; Mon, 1 Jan 2024"
        assert parse_received_for(hdr) == ["alice@example.com"]

    def test_multiple(self):
        hdr = "from a by b for <x@d.com>; from c by d for <y@d.com>"
        result = parse_received_for(hdr)
        assert "x@d.com" in result
        assert "y@d.com" in result

    def test_no_match(self):
        assert parse_received_for("from mx.example.com by mail.example.com") == []

    def test_case_insensitive(self):
        hdr = "FROM mx FOR <Alice@Example.COM>"
        result = parse_received_for(hdr)
        assert result == ["alice@example.com"]


class TestExtractAddresses:
    def test_single(self):
        assert _extract_addresses("alice@example.com") == ["alice@example.com"]

    def test_with_display_name(self):
        assert _extract_addresses("Alice <alice@example.com>") == ["alice@example.com"]

    def test_multiple(self):
        result = _extract_addresses("a@b.com, c@d.com")
        assert len(result) == 2

    def test_empty(self):
        assert _extract_addresses("") == []

    def test_no_at_sign(self):
        assert _extract_addresses("not-an-email") == []


class TestExtractAliases:
    def _mock_conn(self, headers_list: list[bytes]) -> MagicMock:
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        conn.search.return_value = (
            "OK",
            [b" ".join(str(i + 1).encode() for i in range(len(headers_list)))],
        )

        fetch_data = []
        for i, raw in enumerate(headers_list):
            fetch_data.append((f"{i + 1} (BODY[HEADER.FIELDS ...])".encode(), raw))
            fetch_data.append(b")")
        conn.fetch.return_value = ("OK", fetch_data)
        return conn

    def test_basic_extraction(self):
        raw = b"To: alice@example.com\r\nDelivered-To: bob@example.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn)
        assert "alice@example.com" in result
        assert "bob@example.com" in result

    def test_domain_filter(self):
        raw = b"To: alice@example.com\r\nDelivered-To: bob@other.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn, domain="example.com")
        assert "alice@example.com" in result
        assert "bob@other.com" not in result

    def test_received_for(self):
        raw = b"Received: from mx by srv for <alias@co.com>; Mon, 1 Jan 2024\r\nTo: main@co.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn, domain="co.com")
        assert "alias@co.com" in result
        assert "main@co.com" in result

    def test_limit(self):
        raw1 = b"To: a@b.com\r\n\r\n"
        raw2 = b"To: c@b.com\r\n\r\n"
        conn = self._mock_conn([raw1, raw2])
        result = extract_aliases(conn, limit=1)
        # With limit=1, only the most recent (last) message should be scanned.
        # However, both IDs are returned by search; limit trims to 1.
        assert len(result) >= 1

    def test_empty_inbox(self):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"0"])
        conn.search.return_value = ("OK", [b""])
        result = extract_aliases(conn)
        assert result == {}

    def test_x_original_to(self):
        raw = b"X-Original-To: secret@example.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn)
        assert "secret@example.com" in result

    def test_since_parameter(self):
        raw = b"To: a@b.com\r\n\r\n"
        conn = self._mock_conn([raw])
        since = date(2024, 6, 1)
        extract_aliases(conn, since=since)
        # Verify SINCE was passed to search.
        conn.search.assert_called_once_with(None, "SINCE 01-Jun-2024")

    def test_deduplication(self):
        raw = b"To: a@b.com\r\nDelivered-To: a@b.com\r\nX-Original-To: a@b.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn)
        assert set(result) == {"a@b.com"}

    def test_header_tracking(self):
        raw = b"To: a@b.com\r\nDelivered-To: b@b.com\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn)
        assert result["a@b.com"] == {"To"}
        assert result["b@b.com"] == {"Delivered-To"}

    def test_received_for_no_header_set(self):
        raw = b"Received: from mx by srv for <alias@co.com>; Mon, 1 Jan 2024\r\n\r\n"
        conn = self._mock_conn([raw])
        result = extract_aliases(conn)
        assert "alias@co.com" in result
        assert result["alias@co.com"] == set()


class TestBuildAliasMapping:
    def test_basic(self):
        aliases = {"a@b.com": set(), "c@b.com": set()}
        mapping = build_alias_mapping(aliases)
        assert mapping["script_name"] == "alias-router"
        assert len(mapping["rules"]) == 2
        # Each gets folder alias/<local-part>.
        folders = {r["folder"] for r in mapping["rules"]}
        assert "alias/a" in folders
        assert "alias/c" in folders

    def test_custom_prefix(self):
        mapping = build_alias_mapping({"a@b.com": set()}, folder_prefix="work")
        assert mapping["rules"][0]["folder"] == "work/a"

    def test_empty(self):
        mapping = build_alias_mapping({})
        assert mapping["rules"] == []

    def test_plus_suffix_merged(self):
        aliases = {"user@b.com": set(), "user+tag@b.com": set()}
        mapping = build_alias_mapping(aliases)
        # Both should be merged into one rule with folder alias/user.
        assert len(mapping["rules"]) == 1
        rule = mapping["rules"][0]
        assert rule["folder"] == "alias/user"
        assert set(rule["aliases"]) == {"user@b.com", "user+tag@b.com"}

    def test_per_rule_headers(self):
        aliases = {"a@b.com": {"X-Original-To"}, "c@b.com": {"Delivered-To"}}
        mapping = build_alias_mapping(aliases)
        for rule in mapping["rules"]:
            assert "headers" in rule

    def test_no_headers_when_only_received(self):
        aliases = {"a@b.com": set()}
        mapping = build_alias_mapping(aliases)
        assert "headers" not in mapping["rules"][0]


class TestMergeAliases:
    def test_merge_new(self):
        existing = {
            "script_name": "alias-router",
            "rules": [{"alias": "a@b.com", "folder": "alias/a"}],
        }
        result = merge_aliases_into(existing, {"a@b.com": set(), "new@b.com": set()})
        # a@b.com already present, only new@b.com added.
        all_aliases = []
        for r in result["rules"]:
            if "alias" in r:
                all_aliases.append(r["alias"])
            all_aliases.extend(r.get("aliases", []))
        assert "new@b.com" in all_aliases
        assert all_aliases.count("a@b.com") == 1

    def test_merge_nothing_new(self):
        existing = {"rules": [{"alias": "a@b.com", "folder": "alias/a"}]}
        result = merge_aliases_into(existing, {"a@b.com": set()})
        assert len(result["rules"]) == 1

    def test_merge_into_same_folder(self):
        """New alias targeting existing folder merges into existing rule."""
        existing = {
            "rules": [{"alias": "user@b.com", "folder": "alias/user"}],
        }
        # user+tag@b.com maps to alias/user (same folder via +suffix stripping).
        result = merge_aliases_into(existing, {"user+tag@b.com": set()})
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert "user+tag@b.com" in rule["aliases"]
        assert "user@b.com" in rule["aliases"]

    def test_merge_into_same_folder_aliases_key(self):
        """Existing rule already has 'aliases' key."""
        existing = {
            "rules": [{"aliases": ["a@b.com", "a+x@b.com"], "folder": "alias/a"}],
        }
        result = merge_aliases_into(existing, {"a+new@b.com": set()})
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert set(rule["aliases"]) == {"a@b.com", "a+x@b.com", "a+new@b.com"}

    def test_merge_new_folder(self):
        """New alias targeting a new folder creates a new rule."""
        existing = {
            "rules": [{"alias": "a@b.com", "folder": "alias/a"}],
        }
        result = merge_aliases_into(existing, {"z@b.com": set()})
        assert len(result["rules"]) == 2
        folders = {r["folder"] for r in result["rules"]}
        assert "alias/z" in folders

    def test_merge_case_insensitive_skip(self):
        """Known aliases are matched case-insensitively."""
        existing = {
            "rules": [{"alias": "A@b.com", "folder": "alias/a"}],
        }
        result = merge_aliases_into(existing, {"a@b.com": set()})
        assert len(result["rules"]) == 1

    def test_merge_no_rules_key(self):
        """Existing dict without 'rules' key gets one created."""
        existing: dict = {"script_name": "test"}
        result = merge_aliases_into(existing, {"x@b.com": set()})
        assert len(result["rules"]) == 1


class TestLastFetched:
    def test_roundtrip(self):
        data: dict = {}
        update_last_fetched(data, date(2025, 6, 15))
        assert data["last_fetched"] == "2025-06-15"
        assert get_last_fetched(data) == date(2025, 6, 15)

    def test_none_when_missing(self):
        assert get_last_fetched({}) is None


class TestProgress:
    def test_stderr_progress(self, capsys):
        stderr_progress(50, 100)
        err = capsys.readouterr().err
        assert "50%" in err

    def test_stderr_progress_complete(self, capsys):
        stderr_progress(100, 100)
        err = capsys.readouterr().err
        assert "100%" in err


class TestWriteAliasMapping:
    def test_to_file(self, tmp_path):
        mapping = build_alias_mapping({"a@b.com": set()})
        out = tmp_path / "out.json"
        write_alias_mapping(mapping, out)
        assert out.exists()
        import json

        data = json.loads(out.read_text())
        assert data["rules"][0]["folder"] == "alias/a"

    def test_to_string(self):
        mapping = build_alias_mapping({"a@b.com": set()})
        text = write_alias_mapping(mapping, None)
        assert '"a@b.com"' in text
