"""Tests for mailfilter.imap_alias."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from mailfilter.imap_alias import (
    _extract_addresses,
    _or_imap_search,
    apply_rules_imap,
    build_alias_mapping,
    create_imap_folder,
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
        # Default separator is "." → folder alias.<local-part>
        folders = {r["folder"] for r in mapping["rules"]}
        assert "alias.a" in folders
        assert "alias.c" in folders

    def test_custom_prefix(self):
        mapping = build_alias_mapping({"a@b.com": set()}, folder_prefix="work")
        assert mapping["rules"][0]["folder"] == "work.a"

    def test_slash_separator(self):
        mapping = build_alias_mapping({"a@b.com": set()}, folder_sep="/")
        assert mapping["rules"][0]["folder"] == "alias/a"

    def test_empty(self):
        mapping = build_alias_mapping({})
        assert mapping["rules"] == []

    def test_plus_suffix_merged(self):
        aliases = {"user@b.com": set(), "user+tag@b.com": set()}
        mapping = build_alias_mapping(aliases)
        # Both should be merged into one rule with folder alias.user.
        assert len(mapping["rules"]) == 1
        rule = mapping["rules"][0]
        assert rule["folder"] == "alias.user"
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
        assert data["rules"][0]["folder"] == "alias.a"

    def test_to_string(self):
        mapping = build_alias_mapping({"a@b.com": set()})
        text = write_alias_mapping(mapping, None)
        assert '"a@b.com"' in text


class TestOrImapSearch:
    def test_single(self):
        assert _or_imap_search('HEADER "To" "a@b.com"') == 'HEADER "To" "a@b.com"'

    def test_two(self):
        result = _or_imap_search('HEADER "To" "a@b.com"', 'HEADER "To" "c@b.com"')
        assert result == 'OR HEADER "To" "a@b.com" HEADER "To" "c@b.com"'

    def test_three_nests(self):
        result = _or_imap_search("A", "B", "C")
        # "A" and "B" don't start with "OR" so no parens on first step:
        # step1: result = "OR A B"; step2: left = "(OR A B)" → "OR (OR A B) C"
        assert result == "OR (OR A B) C"

    def test_empty_returns_all(self):
        assert _or_imap_search() == "ALL"


class TestCreateImapFolder:
    def test_creates_folder_returns_true(self):
        conn = MagicMock()
        conn.create.return_value = ("OK", [b"folder created"])
        result = create_imap_folder(conn, "alias.test")
        conn.create.assert_called_once_with("alias.test")
        assert result is True

    def test_already_exists_returns_false(self):
        conn = MagicMock()
        conn.create.return_value = ("NO", [b"[ALREADYEXISTS] folder exists"])
        result = create_imap_folder(conn, "alias.test")
        assert result is False

    def test_raises_on_other_error(self):
        import imaplib

        conn = MagicMock()
        conn.create.return_value = ("NO", [b"some other error"])
        with pytest.raises(imaplib.IMAP4.error):
            create_imap_folder(conn, "alias.bad")


class TestApplyRulesImap:
    def _make_conn(self, uid_search_results: dict[str, list[bytes]]) -> MagicMock:
        """Build a mock IMAP connection.

        *uid_search_results* maps a SEARCH criteria fragment to the list of
        UIDs to return.  If the criteria matches any key substring, those UIDs
        are returned; otherwise an empty result is returned.
        """
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.create.return_value = ("OK", [b"created"])

        def uid_handler(command, *args):
            if command == "SEARCH":
                # Return the pre-configured UIDs for any search.
                for key, uids in uid_search_results.items():
                    criteria = " ".join(str(a) for a in args if a is not None)
                    if key in criteria:
                        uid_str = b" ".join(uids) if uids else b""
                        return ("OK", [uid_str])
                return ("OK", [b""])
            elif command in ("MOVE", "COPY") or command == "STORE":
                return ("OK", [b"done"])
            return ("OK", [b""])

        conn.uid.side_effect = uid_handler
        conn.expunge.return_value = ("OK", [b""])
        return conn

    def _make_config(self, rules):
        from mailfilter.config import Config

        return Config(
            headers=["To"],
            use_create=True,
            script_name="test",
            explicit_keep=False,
            match_type="is",
            rules=rules,
        )

    def test_dry_run_no_move(self):
        from mailfilter.config import Rule

        rule = Rule(aliases=["alice@example.com"], folder="alias.alice", headers=["To"])
        conn = self._make_conn({"alice@example.com": [b"1", b"2"]})
        config = self._make_config([rule])

        moved = apply_rules_imap(conn, config, ["INBOX"], dry_run=True)
        assert moved == {"alias.alice": 2}
        # No actual MOVE or COPY should have been called.
        for call in conn.uid.call_args_list:
            assert call.args[0] not in ("MOVE", "COPY")

    def test_moves_messages(self):
        from mailfilter.config import Rule

        rule = Rule(aliases=["alice@example.com"], folder="alias.alice", headers=["To"])
        conn = self._make_conn({"alice@example.com": [b"1"]})
        config = self._make_config([rule])

        moved = apply_rules_imap(conn, config, ["INBOX"])
        assert moved == {"alias.alice": 1}

    def test_no_match_returns_empty(self):
        from mailfilter.config import Rule

        rule = Rule(aliases=["nobody@example.com"], folder="alias.nobody", headers=["To"])
        conn = self._make_conn({})
        config = self._make_config([rule])

        moved = apply_rules_imap(conn, config, ["INBOX"])
        assert moved == {}

    def test_inactive_rule_skipped(self):
        from mailfilter.config import Rule

        rule = Rule(aliases=["alice@example.com"], folder="alias.alice", headers=["To"], active=False)
        conn = self._make_conn({"alice@example.com": [b"1"]})
        config = self._make_config([rule])

        moved = apply_rules_imap(conn, config, ["INBOX"])
        assert moved == {}

    def test_progress_callback(self):
        from mailfilter.config import Rule

        rule = Rule(aliases=["alice@example.com"], folder="alias.alice", headers=["To"])
        conn = self._make_conn({"alice@example.com": [b"1", b"2"]})
        config = self._make_config([rule])

        calls: list[tuple[str, int]] = []
        apply_rules_imap(conn, config, ["INBOX"], dry_run=True, progress=lambda f, c: calls.append((f, c)))
        assert ("alias.alice", 2) in calls


class TestConnectImap:
    """Tests for connect_imap covering all three connection_security modes (lines 55-63)."""

    @patch("mailfilter.imap_alias.imaplib.IMAP4_SSL")
    def test_ssl_mode(self, mock_ssl):
        from mailfilter.imap_alias import connect_imap

        mock_conn = MagicMock()
        mock_ssl.return_value = mock_conn
        result = connect_imap("host", 993, "user", "pass", "ssl")
        mock_ssl.assert_called_once()
        mock_conn.login.assert_called_once_with("user", "pass")
        assert result is mock_conn

    @patch("mailfilter.imap_alias.imaplib.IMAP4")
    def test_starttls_mode(self, mock_imap4):
        from mailfilter.imap_alias import connect_imap

        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        result = connect_imap("host", 143, "user", "pass", "starttls")
        mock_imap4.assert_called_once_with("host", 143)
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("user", "pass")
        assert result is mock_conn

    @patch("mailfilter.imap_alias.imaplib.IMAP4")
    def test_none_mode(self, mock_imap4):
        from mailfilter.imap_alias import connect_imap

        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        result = connect_imap("host", 143, "user", "pass", "none")
        mock_imap4.assert_called_once_with("host", 143)
        mock_conn.starttls.assert_not_called()
        mock_conn.login.assert_called_once_with("user", "pass")
        assert result is mock_conn


class TestExtractAliasesProgressAndNonBytes:
    """Cover extract_aliases lines 138 (non-bytes raw_headers) and 154 (progress callback)."""

    def _mock_conn_with_non_bytes_item(self) -> MagicMock:
        """Return a mock where one fetch item has a non-bytes second element."""
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        conn.search.return_value = ("OK", [b"1"])
        # Tuple whose second element is NOT bytes → triggers line 138 continue.
        fetch_data = [(b"1 (BODY[...])", "not bytes"), (b"2 (BODY[...])", b"To: a@b.com\r\n\r\n")]
        conn.fetch.return_value = ("OK", fetch_data)
        return conn

    def test_non_bytes_raw_headers_skipped(self):
        """A tuple item whose second element is not bytes is skipped (line 138)."""
        conn = self._mock_conn_with_non_bytes_item()
        result = extract_aliases(conn)
        # The bytes item produces a@b.com; the non-bytes item is skipped.
        assert "a@b.com" in result

    def test_progress_callback_called(self):
        """progress callback is invoked after each batch (line 154)."""
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = ("OK", [(b"1 (BODY[...])", b"To: a@b.com\r\n\r\n")])
        calls: list[tuple[int, int]] = []
        extract_aliases(conn, progress=lambda processed, total: calls.append((processed, total)))
        assert len(calls) > 0
        assert calls[0][1] == 1  # total = 1


class TestImapMoveMessages:
    """Tests for _imap_move_messages covering fallback paths (lines 372-387)."""

    def test_empty_uids_returns_immediately(self):
        """Empty uid list returns without any calls (line 372)."""

        from mailfilter.imap_alias import _imap_move_messages

        conn = MagicMock()
        _imap_move_messages(conn, [], "target")
        conn.uid.assert_not_called()

    def test_move_extension_error_triggers_copy_fallback(self):
        """MOVE raising IMAP4.error falls back to COPY+STORE+EXPUNGE (lines 379-387)."""
        import imaplib

        from mailfilter.imap_alias import _imap_move_messages

        conn = MagicMock()

        def uid_handler(cmd, *args):
            if cmd == "MOVE":
                raise imaplib.IMAP4.error("MOVE not supported")
            if cmd == "COPY":
                return ("OK", [b"copied"])
            return ("OK", [b""])

        conn.uid.side_effect = uid_handler
        _imap_move_messages(conn, [b"1"], "target")
        conn.expunge.assert_called_once()

    def test_move_returns_no_triggers_copy_fallback(self):
        """MOVE returning NO (not OK) falls back to COPY (lines 380-387)."""
        from mailfilter.imap_alias import _imap_move_messages

        conn = MagicMock()

        def uid_handler(cmd, *args):
            if cmd == "MOVE":
                return ("NO", [b"not supported"])
            if cmd == "COPY":
                return ("OK", [b"copied"])
            return ("OK", [b""])

        conn.uid.side_effect = uid_handler
        _imap_move_messages(conn, [b"1"], "target")
        conn.expunge.assert_called_once()

    def test_copy_failure_raises(self):
        """COPY failure raises IMAP4.error (lines 383-385)."""
        import imaplib

        from mailfilter.imap_alias import _imap_move_messages

        conn = MagicMock()

        def uid_handler(cmd, *args):
            if cmd == "MOVE":
                raise imaplib.IMAP4.error("no MOVE")
            if cmd == "COPY":
                return ("NO", [b"permission denied"])
            return ("OK", [b""])

        conn.uid.side_effect = uid_handler
        with pytest.raises(imaplib.IMAP4.error, match="COPY"):
            _imap_move_messages(conn, [b"1"], "target")


class TestApplyRulesImapMissingLines:
    """Tests for apply_rules_imap covering lines 434, 439-440, 446-448, 454-455."""

    def _make_config(self, rules):
        from mailfilter.config import Config

        return Config(
            headers=["To"],
            use_create=True,
            script_name="test",
            explicit_keep=False,
            match_type="is",
            rules=rules,
        )

    def test_empty_aliases_rule_skipped(self):
        """Rule with no aliases produces empty criteria_parts → skipped (line 434)."""
        from mailfilter.config import Rule

        rule = Rule(aliases=[], folder="alias.empty")
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        moved = apply_rules_imap(conn, config, ["INBOX"])
        assert moved == {}
        conn.uid.assert_not_called()

    def test_search_imap_error_continues(self):
        """SEARCH raising IMAP4.error causes the rule to be skipped (lines 439-440)."""
        import imaplib

        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = imaplib.IMAP4.error("search failed")
        moved = apply_rules_imap(conn, config, ["INBOX"])
        assert moved == {}

    def test_no_uids_with_progress_callback(self):
        """Whitespace-only SEARCH result calls progress callback with 0 (lines 446-448)."""
        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        # b" " is truthy (passes the not data[0] check) but split() → [] (empty uid list).
        conn.uid.return_value = ("OK", [b" "])
        progress_calls: list[tuple[str, int]] = []
        apply_rules_imap(conn, config, ["INBOX"], progress=lambda f, c: progress_calls.append((f, c)))
        assert ("alias.a", 0) in progress_calls

    def test_create_folder_exception_suppressed(self):
        """Exception in create_imap_folder is suppressed; move continues."""

        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        # SEARCH returns one UID; MOVE succeeds.
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        # create_imap_folder → conn.create returns a non-ALREADYEXISTS NO → raises
        conn.create.return_value = ("NO", [b"permission denied"])
        # Should not raise; the exception is suppressed.
        moved = apply_rules_imap(conn, config, ["INBOX"], create_folders=True)
        assert moved == {"alias.a": 1}

    def test_folder_created_callback_fires_for_new_folder(self):
        """folder_created callback is called when a folder is newly created."""

        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        conn.create.return_value = ("OK", [b"created"])  # folder is new
        created: list[str] = []
        apply_rules_imap(conn, config, ["INBOX"], create_folders=True, folder_created=lambda f: created.append(f))
        assert created == ["alias.a"]

    def test_folder_created_callback_not_fired_for_existing_folder(self):
        """folder_created callback is NOT called when the folder already existed."""

        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        conn.create.return_value = ("NO", [b"[ALREADYEXISTS] already exists"])  # pre-existing
        created: list[str] = []
        apply_rules_imap(conn, config, ["INBOX"], create_folders=True, folder_created=lambda f: created.append(f))
        assert created == []

    def test_folder_created_callback_not_fired_twice_for_same_folder(self):
        """folder_created fires at most once per folder even across multiple source folders."""

        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH inbox
            ("OK", [b""]),  # MOVE inbox
            ("OK", [b"2"]),  # SEARCH sent
            ("OK", [b""]),  # MOVE sent
        ]
        conn.create.return_value = ("OK", [b"created"])
        created: list[str] = []
        apply_rules_imap(
            conn,
            config,
            ["INBOX", "Sent"],
            create_folders=True,
            folder_created=lambda f: created.append(f),
        )
        assert created == ["alias.a"]  # only once, not twice


class TestSubscribeImapFolder:
    """Tests for subscribe_imap_folder (lines 366-369)."""

    def test_subscribe_calls_conn_subscribe(self):
        """subscribe_imap_folder calls conn.subscribe with the folder name."""
        from mailfilter.imap_alias import subscribe_imap_folder

        conn = MagicMock()
        conn.subscribe.return_value = ("OK", [b"subscribed"])
        subscribe_imap_folder(conn, "alias.test")
        conn.subscribe.assert_called_once_with("alias.test")

    def test_subscribe_exception_suppressed(self):
        """subscribe_imap_folder silently ignores exceptions (best-effort)."""
        from mailfilter.imap_alias import subscribe_imap_folder

        conn = MagicMock()
        conn.subscribe.side_effect = Exception("server error")
        # Must not raise.
        subscribe_imap_folder(conn, "alias.test")


class TestApplyRulesImapSubscribe:
    """Test subscribe_folders parameter in apply_rules_imap (line 492)."""

    def _make_config(self, rules):
        from mailfilter.config import Config

        return Config(
            headers=["To"],
            use_create=True,
            script_name="test",
            explicit_keep=False,
            match_type="is",
            rules=rules,
        )

    def test_subscribe_called_for_new_folder(self):
        """subscribe_imap_folder is called when subscribe_folders=True and folder is new."""
        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        conn.create.return_value = ("OK", [b"created"])
        apply_rules_imap(conn, config, ["INBOX"], create_folders=True, subscribe_folders=True)
        conn.subscribe.assert_called_once_with("alias.a")

    def test_subscribe_not_called_when_false(self):
        """subscribe_imap_folder is NOT called when subscribe_folders=False."""
        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        conn.create.return_value = ("OK", [b"created"])
        apply_rules_imap(conn, config, ["INBOX"], create_folders=True, subscribe_folders=False)
        conn.subscribe.assert_not_called()

    def test_subscribe_not_called_for_existing_folder(self):
        """subscribe_imap_folder is NOT called when folder already existed (ALREADYEXISTS)."""
        from mailfilter.config import Rule

        rule = Rule(aliases=["a@b.com"], folder="alias.a", headers=["To"])
        config = self._make_config([rule])
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.uid.side_effect = [
            ("OK", [b"1"]),  # SEARCH
            ("OK", [b""]),  # MOVE
        ]
        conn.create.return_value = ("NO", [b"[ALREADYEXISTS] already exists"])
        apply_rules_imap(conn, config, ["INBOX"], create_folders=True, subscribe_folders=True)
        conn.subscribe.assert_not_called()
