"""ManageSieve (RFC 5804) client for uploading and activating Sieve scripts."""

from __future__ import annotations

import base64
import re
import socket
import ssl
from collections.abc import Sequence
from typing import Any

from mailfilter.sieve import sieve_quote


class ManageSieveError(RuntimeError):
    """Raised when the ManageSieve server returns an error response."""


class ManageSieveClient:
    """Low-level ManageSieve (RFC 5804) client.

    Usage::

        with ManageSieveClient(host, port, connection_security="ssl") as client:
            client.authenticate_plain(username, password)
            client.put_script("myscript", script_text)
            client.set_active("myscript")
    """

    def __init__(
        self,
        host: str,
        port: int,
        connection_security: str = "ssl",
        insecure: bool = False,
        timeout: float = 15.0,
    ) -> None:
        """Initialise the client (does not connect yet).

        Args:
            host: ManageSieve server hostname.
            port: TCP port (RFC 5804 default: 4190).
            connection_security: ``ssl`` for implicit TLS, ``starttls`` for
                STARTTLS negotiation, ``none`` for plaintext (warns).
            insecure: When ``True``, skip TLS certificate verification.
            timeout: Socket timeout in seconds.
        """
        self.host = host
        self.port = port
        self.connection_security = connection_security
        self.insecure = insecure
        self.timeout = timeout
        self.sock: socket.socket | ssl.SSLSocket | None = None
        self.file: Any = None
        self.capabilities: dict[str, str | None] = {}

    def __enter__(self) -> ManageSieveClient:
        """Connect and return self."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Disconnect on context-manager exit, suppressing logout errors."""
        try:
            if self.sock:
                try:
                    self.send_command("LOGOUT")
                    self.read_response_block()
                except Exception:
                    pass
                self.sock.close()
        finally:
            self.sock = None
            self.file = None

    def connect(self) -> None:
        """Open the TCP connection, perform TLS setup, and read server greeting.

        Raises:
            :class:`ManageSieveError`: If the server greeting is not ``OK`` or
                if STARTTLS negotiation fails.
        """
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw.settimeout(self.timeout)

        if self.connection_security == "ssl":
            ctx = self._tls_context()
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw

        self.file = self.sock.makefile("rwb", buffering=0)
        lines, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"unexpected greeting status: {final}")
        self.capabilities = self._parse_capabilities(lines)

        if self.connection_security == "starttls":
            self.starttls()

    def starttls(self) -> None:
        """Upgrade the current plaintext connection to TLS via STARTTLS."""
        self.send_command("STARTTLS")
        _, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"STARTTLS failed: {final}")
        if self.sock is None:
            raise ManageSieveError("not connected")
        self.sock = self._tls_context().wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("rwb", buffering=0)
        lines, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"post-STARTTLS capability failed: {final}")
        self.capabilities = self._parse_capabilities(lines)

    def authenticate_plain(self, username: str, password: str, authz_id: str = "") -> None:
        """Authenticate using SASL PLAIN.

        Args:
            username: Login identity.
            password: Login credential.
            authz_id: Optional SASL authorisation identity (usually empty).

        Raises:
            :class:`ManageSieveError`: If the server does not advertise SASL
                PLAIN or authentication is rejected.
        """
        sasl = self.capabilities.get("SASL") or ""
        if "PLAIN" not in sasl.split():
            raise ManageSieveError(f"server does not advertise SASL PLAIN; capabilities: {self.capabilities}")
        payload = base64.b64encode(f"{authz_id}\x00{username}\x00{password}".encode()).decode("ascii")
        self.send_command(f'AUTHENTICATE "PLAIN" "{payload}"')
        lines, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"authentication failed: {final}; extra={lines}")

    def check_script(self, script_text: str) -> None:
        """Validate *script_text* on the server without storing it.

        Raises:
            :class:`ManageSieveError`: If the server reports a syntax error.
        """
        self.send_command(f"CHECKSCRIPT {self._literal(script_text)}", raw=True)
        _, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"CHECKSCRIPT failed: {final}")

    def put_script(self, script_name: str, script_text: str) -> None:
        """Upload *script_text* as *script_name* to the server.

        Raises:
            :class:`ManageSieveError`: If the server rejects the upload.
        """
        self.send_command(f"PUTSCRIPT {sieve_quote(script_name)} {self._literal(script_text)}", raw=True)
        _, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"PUTSCRIPT failed: {final}")

    def set_active(self, script_name: str) -> None:
        """Activate *script_name* on the server.

        Raises:
            :class:`ManageSieveError`: If the server rejects the activation.
        """
        self.send_command(f"SETACTIVE {sieve_quote(script_name)}")
        _, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"SETACTIVE failed: {final}")

    def list_scripts(self) -> list[tuple[str, bool]]:
        """Return a list of ``(script_name, is_active)`` pairs from the server.

        Raises:
            :class:`ManageSieveError`: On a server error response.
        """
        self.send_command("LISTSCRIPTS")
        lines, final = self.read_response_block()
        if final[0] != "OK":
            raise ManageSieveError(f"LISTSCRIPTS failed: {final}")

        scripts: list[tuple[str, bool]] = []
        for line in lines:
            match = re.match(r'^"((?:[^"\\]|\\.)*)"(?:\s+ACTIVE)?$', line, re.IGNORECASE)
            if not match:
                continue
            name = match.group(1).encode("utf-8").decode("unicode_escape")
            active = line.upper().endswith(" ACTIVE")
            scripts.append((name, active))
        return scripts

    def send_command(self, command: str, raw: bool = False) -> None:
        """Write *command* to the server socket.

        Args:
            command: The ManageSieve command string.
            raw: When ``True`` the command is sent as-is (for literal payloads);
                otherwise ``\r\n`` is appended.

        Raises:
            :class:`ManageSieveError`: If the socket is not connected.
        """
        if self.file is None:
            raise ManageSieveError("not connected")
        data = command.encode("utf-8") if raw else (command + "\r\n").encode("utf-8")
        if raw and not data.endswith(b"\r\n"):
            data += b"\r\n"
        self.file.write(data)
        self.file.flush()

    def read_response_block(self) -> tuple[list[str], tuple[str, str]]:
        """Read server lines until a final ``OK``, ``NO``, or ``BYE`` response.

        Returns:
            A ``(lines, (status, rest))`` tuple where *lines* contains all
            non-final response lines and *status* is ``'OK'``, ``'NO'``, or
            ``'BYE'``.
        """
        lines: list[str] = []
        while True:
            line = self._read_line_text()
            upper = line.upper()
            if upper.startswith("OK"):
                return lines, ("OK", line[2:].strip())
            if upper.startswith("NO"):
                return lines, ("NO", line[2:].strip())
            if upper.startswith("BYE"):
                return lines, ("BYE", line[3:].strip())
            lines.append(line)

    def _read_line_text(self) -> str:
        if self.file is None:
            raise ManageSieveError("not connected")
        raw = self.file.readline()
        if not raw:
            raise ManageSieveError("connection closed by server")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def _tls_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self.insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @staticmethod
    def _literal(text: str) -> str:
        payload = text.encode("utf-8")
        return "{" + str(len(payload)) + "+}\r\n" + text

    @staticmethod
    def _parse_capabilities(lines: Sequence[str]) -> dict[str, str | None]:
        caps: dict[str, str | None] = {}
        pat = re.compile(r'^"((?:[^"\\]|\\.)*)"(?:\s+"((?:[^"\\]|\\.)*)")?$')
        for line in lines:
            match = pat.match(line)
            if not match:
                continue
            key = match.group(1).encode("utf-8").decode("unicode_escape")
            value = match.group(2)
            if value is not None:
                value = value.encode("utf-8").decode("unicode_escape")
            caps[key] = value
        return caps


def upload_via_managesieve(
    host: str,
    port: int,
    username: str,
    password: str,
    script_name: str,
    script_text: str,
    connection_security: str,
    authz_id: str,
    do_check: bool,
    activate: bool,
) -> list[tuple[str, bool]]:
    """Connect to a ManageSieve server and upload a Sieve script.

    Args:
        host: Server hostname.
        port: TCP port.
        username: Login identity.
        password: Login credential.
        script_name: Name to give the script on the server.
        script_text: Full Sieve script content (UTF-8 text).
        connection_security: ``ssl``, ``starttls``, or ``none``.
        authz_id: Optional SASL authorisation identity.
        do_check: When ``True`` run CHECKSCRIPT before uploading.
        activate: When ``True`` activate the script after upload.

    Returns:
        A list of ``(name, is_active)`` pairs from LISTSCRIPTS.

    Raises:
        :class:`ManageSieveError`: On any protocol or authentication failure.
    """
    with ManageSieveClient(host=host, port=port, connection_security=connection_security, insecure=False) as client:
        client.authenticate_plain(username=username, password=password, authz_id=authz_id)
        if do_check:
            client.check_script(script_text)
        client.put_script(script_name, script_text)
        if activate:
            client.set_active(script_name)
        return client.list_scripts()
