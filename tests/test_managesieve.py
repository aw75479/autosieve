"""Tests for mailfilter.managesieve using mocked sockets."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from mailfilter.managesieve import ManageSieveClient, ManageSieveError


def _make_file(lines: list[str]) -> io.BytesIO:
    """Create a BytesIO simulating server responses."""
    data = b"".join((line + "\r\n").encode() for line in lines)
    return io.BytesIO(data)


class TestManageSieveClient:
    def test_parse_capabilities(self):
        lines = [
            '"IMPLEMENTATION" "Example Sieve 1.0"',
            '"SASL" "PLAIN LOGIN"',
            '"SIEVE" "fileinto reject"',
            '"STARTTLS"',
        ]
        caps = ManageSieveClient._parse_capabilities(lines)
        assert caps["IMPLEMENTATION"] == "Example Sieve 1.0"
        assert caps["SASL"] == "PLAIN LOGIN"
        assert caps["SIEVE"] == "fileinto reject"
        assert "STARTTLS" in caps

    def test_literal(self):
        text = "hello world"
        result = ManageSieveClient._literal(text)
        assert result.startswith("{11+}\r\n")
        assert result.endswith("hello world")

    def test_literal_utf8(self):
        text = "Hallo Welt"
        result = ManageSieveClient._literal(text)
        payload_len = len(text.encode("utf-8"))
        assert result.startswith(f"{{{payload_len}+}}\r\n")

    @patch("mailfilter.managesieve.socket.create_connection")
    def test_connect_greeting_fail(self, mock_conn):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.makefile.return_value = _make_file(["NO greeting failed"])

        client = ManageSieveClient("host", 4190, connection_security="none")
        with pytest.raises(ManageSieveError, match="unexpected greeting"):
            client.connect()

    @patch("mailfilter.managesieve.socket.create_connection")
    def test_connect_ok_plain(self, mock_conn):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.makefile.return_value = _make_file(
            [
                '"IMPLEMENTATION" "Test"',
                '"SASL" "PLAIN"',
                "OK",
            ]
        )

        client = ManageSieveClient("host", 4190, connection_security="none")
        client.connect()
        assert client.capabilities["SASL"] == "PLAIN"

    @patch("mailfilter.managesieve.socket.create_connection")
    def test_authenticate_plain_no_mechanism(self, mock_conn):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        # greeting
        mock_sock.makefile.return_value = _make_file(
            [
                '"IMPLEMENTATION" "Test"',
                '"SASL" "LOGIN"',
                "OK",
            ]
        )

        client = ManageSieveClient("host", 4190, connection_security="none")
        client.connect()
        with pytest.raises(ManageSieveError, match="SASL PLAIN"):
            client.authenticate_plain("user", "pass")

    def test_read_response_block_ok(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = _make_file(['"line1"', '"line2"', "OK done"])
        lines, final = client.read_response_block()
        assert lines == ['"line1"', '"line2"']
        assert final == ("OK", "done")

    def test_read_response_block_no(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = _make_file(["NO error message"])
        _lines, final = client.read_response_block()
        assert final[0] == "NO"

    def test_send_command(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        buf = io.BytesIO()
        client.file = buf
        client.send_command("LISTSCRIPTS")
        buf.seek(0)
        assert buf.read() == b"LISTSCRIPTS\r\n"

    def test_send_command_raw(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        buf = io.BytesIO()
        client.file = buf
        client.send_command("PUTSCRIPT {5+}\r\nhello", raw=True)
        buf.seek(0)
        data = buf.read()
        assert data.startswith(b"PUTSCRIPT {5+}\r\nhello")
        assert data.endswith(b"\r\n")

    def test_list_scripts(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = _make_file(['"my-script" ACTIVE', '"backup"', "OK"])
        client.send_command = MagicMock()
        scripts = client.list_scripts()
        assert ("my-script", True) in scripts
        assert ("backup", False) in scripts
