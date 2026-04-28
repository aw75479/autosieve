"""XOAUTH2 token resolution for IMAP and ManageSieve auth.

Independent module: delete this file to disable OAuth2 support.

Configuration (TOML, per target)::

    [targets.imap]
    auth = "xoauth2"      # instead of "plain"
    user = "me@gmail.com"

    [targets.features.oauth2]
    # Strategy 1: external command -- portable, no deps. The command is
    # spawned and its STDOUT (stripped) is used as the bearer token.
    token_command = "/usr/local/bin/get-gmail-token me@gmail.com"

    # Strategy 2 (planned): built-in device-code flow for known providers.
    # Set provider = "gmail" or "microsoft" and supply your own client_id
    # via the GOOGLE_OAUTH_CLIENT_ID / MS_OAUTH_CLIENT_ID env var.
    provider     = "gmail"      # one of: gmail, microsoft
    cache_in_keyring = true     # store the refresh token in the keyring

When ``imap.auth == "xoauth2"`` or ``managesieve.auth == "xoauth2"`` the
caller resolves a bearer token via :func:`get_xoauth2_token` and uses the
SASL XOAUTH2 mechanism with the IMAP / ManageSieve server.

Security note
=============
The built-in device-code strategy is intentionally **not** shipped with
embedded client credentials -- using a public default would expose all
users to revocation.  Set your own client_id (and, for Microsoft,
tenant_id) in the configuration or environment.  Until you do, only the
``token_command`` strategy is operational.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any


class OAuth2Error(RuntimeError):
    """Raised when an OAuth2 token cannot be resolved."""


def get_xoauth2_token(target: Any) -> str:
    """Resolve a bearer token for *target* (IMAP or ManageSieve auth)."""
    cfg = target.feature_block("oauth2")
    if not cfg:
        raise OAuth2Error(f"target {target.name!r} requires xoauth2 but has no [targets.features.oauth2] block")

    if cfg.get("token_command"):
        return _run_token_command(str(cfg["token_command"]))

    provider = cfg.get("provider")
    if provider in {"gmail", "microsoft"}:
        raise OAuth2Error(
            f"oauth2 provider={provider!r} support is scaffolded but the device-code flow is "
            "not yet implemented; for now please configure 'token_command' with a script "
            "that prints a fresh access token to stdout."
        )

    raise OAuth2Error("oauth2 feature is enabled but no usable strategy is configured (set 'token_command' or 'provider')")


def build_xoauth2_sasl(user: str, token: str) -> str:
    """Build the SASL XOAUTH2 client response string (not base64-encoded)."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def _run_token_command(cmd: str) -> str:
    """Run *cmd* via the shell-compatible split list and return stdout."""
    try:
        proc = subprocess.run(  # noqa: S603 - command comes from trusted local config
            shlex.split(cmd),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise OAuth2Error(f"token_command timed out: {cmd}") from exc
    except subprocess.CalledProcessError as exc:
        raise OAuth2Error(f"token_command failed (exit {exc.returncode}): {exc.stderr.strip()}") from exc
    except FileNotFoundError as exc:
        raise OAuth2Error(f"token_command not found: {cmd}") from exc
    token = proc.stdout.strip()
    if not token:
        raise OAuth2Error("token_command produced empty output")
    return token
