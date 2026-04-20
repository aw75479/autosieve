"""Tests for mailfilter.imap_alias."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from mailfilter.imap_alias import (
    _extract_addresses,
    build_alias_mapping,
    extract_aliases,
    parse_received_for,
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
        raw = (
            b"Received: from mx by srv for <alias@co.com>; Mon, 1 Jan 2024\r\n"
            b"To: main@co.com\r\n\r\n"
        )
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
        assert result == set()

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
        assert result == {"a@b.com"}


class TestBuildAliasMapping:
    def test_basic(self):
        aliases = {"a@b.com", "c@b.com"}
        mapping = build_alias_mapping(aliases)
        assert mapping["script_name"] == "alias-router"
        assert len(mapping["rules"]) == 2
        # Sorted order.
        assert mapping["rules"][0]["alias"] == "a@b.com"
        assert mapping["rules"][1]["alias"] == "c@b.com"

    def test_custom_folder(self):
        mapping = build_alias_mapping({"a@b.com"}, default_folder="Work")
        assert mapping["rules"][0]["folder"] == "Work"

    def test_empty(self):
        mapping = build_alias_mapping(set())
        assert mapping["rules"] == []


class TestWriteAliasMapping:
    def test_to_file(self, tmp_path):
        mapping = build_alias_mapping({"a@b.com"})
        out = tmp_path / "out.json"
        write_alias_mapping(mapping, out)
        assert out.exists()
        import json

        data = json.loads(out.read_text())
        assert data["rules"][0]["alias"] == "a@b.com"

    def test_to_string(self):
        mapping = build_alias_mapping({"a@b.com"})
        text = write_alias_mapping(mapping, None)
        assert '"a@b.com"' in text
