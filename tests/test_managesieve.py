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

    def test_exit_no_sock(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.sock = None
        client.file = None
        client.__exit__(None, None, None)
        assert client.sock is None

    def test_exit_with_sock(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        mock_sock = MagicMock()
        client.sock = mock_sock
        buf = io.BytesIO()
        client.file = buf
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "bye")))
        client.__exit__(None, None, None)
        mock_sock.close.assert_called_once()
        assert client.sock is None
        assert client.file is None

    def test_enter(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        with patch.object(client, "connect"):
            result = client.__enter__()
            assert result is client

    def test_authenticate_plain_success(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.capabilities = {"SASL": "PLAIN LOGIN"}
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        client.authenticate_plain("user", "pass")
        client.send_command.assert_called_once()

    def test_authenticate_plain_with_authz_id(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.capabilities = {"SASL": "PLAIN LOGIN"}
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        client.authenticate_plain("user", "pass", authz_id="admin")
        cmd = client.send_command.call_args[0][0]
        assert "AUTHENTICATE" in cmd

    def test_authenticate_plain_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.capabilities = {"SASL": "PLAIN"}
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=(["detail"], ("NO", "bad credentials")))
        with pytest.raises(ManageSieveError, match="authentication failed"):
            client.authenticate_plain("user", "wrong")

    def test_check_script_success(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        client.check_script('require ["fileinto"];')

    def test_check_script_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("NO", "syntax error")))
        with pytest.raises(ManageSieveError, match="CHECKSCRIPT"):
            client.check_script("invalid")

    def test_put_script_success(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        client.put_script("test", 'require ["fileinto"];')

    def test_put_script_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("NO", "quota exceeded")))
        with pytest.raises(ManageSieveError, match="PUTSCRIPT"):
            client.put_script("test", "content")

    def test_set_active_success(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        client.set_active("test")

    def test_set_active_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("NO", "no such script")))
        with pytest.raises(ManageSieveError, match="SETACTIVE"):
            client.set_active("nonexistent")

    def test_list_scripts_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("NO", "error")))
        with pytest.raises(ManageSieveError, match="LISTSCRIPTS"):
            client.list_scripts()

    def test_tls_context_secure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.insecure = False
        ctx = client._tls_context()
        assert ctx.check_hostname is True

    def test_tls_context_insecure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.insecure = True
        ctx = client._tls_context()
        assert ctx.check_hostname is False

    def test_send_command_not_connected(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = None
        with pytest.raises(ManageSieveError, match="not connected"):
            client.send_command("NOOP")

    def test_read_line_text_closed(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = io.BytesIO(b"")
        with pytest.raises(ManageSieveError, match="connection closed"):
            client._read_line_text()

    def test_read_line_text_not_connected(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = None
        with pytest.raises(ManageSieveError, match="not connected"):
            client._read_line_text()

    def test_read_response_block_bye(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.file = _make_file(["BYE server going down"])
        _lines, final = client.read_response_block()
        assert final[0] == "BYE"

    @patch("mailfilter.managesieve.socket.create_connection")
    def test_starttls(self, mock_conn):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.makefile.return_value = _make_file(['"SASL" "PLAIN"', '"STARTTLS"', "OK"])

        client = ManageSieveClient("host", 4190, connection_security="none")
        client.connect()

        # Now mock the starttls flow.
        responses = iter(
            [
                ([], ("OK", "")),  # STARTTLS response
                (['"SASL" "PLAIN"'], ("OK", "")),  # post-TLS capabilities
            ]
        )
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(side_effect=lambda: next(responses))
        with patch.object(client, "_tls_context") as mock_ctx:
            mock_ctx.return_value.wrap_socket.return_value = MagicMock()
            mock_ctx.return_value.wrap_socket.return_value.makefile.return_value = _make_file([])
            client.starttls()

    def test_starttls_not_connected(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.sock = None
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("OK", "")))
        with pytest.raises(ManageSieveError, match="not connected"):
            client.starttls()

    def test_starttls_failure(self):
        client = ManageSieveClient.__new__(ManageSieveClient)
        client.sock = MagicMock()
        client.send_command = MagicMock()
        client.read_response_block = MagicMock(return_value=([], ("NO", "denied")))
        with pytest.raises(ManageSieveError, match="STARTTLS failed"):
            client.starttls()


class TestUploadViaManageSieve:
    @patch("mailfilter.managesieve.ManageSieveClient")
    def test_basic(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.list_scripts.return_value = [("test", True)]

        from mailfilter.managesieve import upload_via_managesieve

        result = upload_via_managesieve(
            host="h",
            port=4190,
            username="u",
            password="p",
            script_name="test",
            script_text="content",
            connection_security="ssl",
            insecure=False,
            authz_id="",
            do_check=True,
            activate=True,
        )
        assert result == [("test", True)]
        mock_client.authenticate_plain.assert_called_once()
        mock_client.check_script.assert_called_once()
        mock_client.put_script.assert_called_once()
        mock_client.set_active.assert_called_once()

    @patch("mailfilter.managesieve.ManageSieveClient")
    def test_no_check_no_activate(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.list_scripts.return_value = []

        from mailfilter.managesieve import upload_via_managesieve

        upload_via_managesieve(
            host="h",
            port=4190,
            username="u",
            password="p",
            script_name="test",
            script_text="content",
            connection_security="none",
            insecure=True,
            authz_id="admin",
            do_check=False,
            activate=False,
        )
        mock_client.check_script.assert_not_called()
        mock_client.set_active.assert_not_called()
