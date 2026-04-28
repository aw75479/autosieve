"""End-to-end ``sync`` command: extract -> generate -> apply -> upload.

This module is **independent** and can be removed by deleting this file and
the corresponding line in :func:`autosieve.cli.main`.

The ``sync`` command runs the existing four pipeline stages back-to-back
against a single target.  By default it asks for confirmation before each
mutating step (``apply`` and ``upload``); ``--yes`` skips prompts for the
non-destructive steps but the destructive :command:`apply` step still
requires a separate ``--yes-apply`` (or interactive ``y/N``) since it moves
real mail.  ``--dry-run`` propagates to all stages.

Step skipping flags (``--no-extract``, ``--no-apply``, ``--no-upload``)
allow trimming the pipeline; e.g. ``sync --no-apply`` is the standard
"refresh aliases and update server script" workflow.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``sync`` subparser on the top-level argparse object."""
    p = subparsers.add_parser(
        "sync",
        help="Run extract -> generate -> apply -> upload as a single pipeline (with prompts).",
        description=(
            "Convenience wrapper that chains 'extract', 'generate', 'apply' and 'upload'. "
            "By default each step prompts for confirmation; --yes skips prompts for "
            "non-destructive steps. The 'apply' step (which moves mail) always requires "
            "an extra confirmation unless --yes-apply is given."
        ),
    )
    # Match the existing subcommand flags so users can pass the same things.
    from autosieve.cli import _add_password_args, _add_target_arg

    p.add_argument("--config", type=__import__("pathlib").Path, help="Server config TOML file")
    _add_target_arg(p)
    p.add_argument("-y", "--yes", action="store_true", help="Skip prompts for non-destructive steps (extract/generate/upload)")
    p.add_argument("--yes-apply", action="store_true", help="Also skip prompt for the destructive 'apply' step (moves mail). Use with care.")
    p.add_argument("--dry-run", action="store_true", help="Run all steps in dry-run mode where supported")
    p.add_argument("--no-extract", action="store_true", help="Skip the IMAP alias extraction step")
    p.add_argument("--no-apply", action="store_true", help="Skip the retroactive IMAP message-move step")
    p.add_argument("--no-upload", action="store_true", help="Skip the ManageSieve upload step")
    _add_password_args(p)
    p.set_defaults(func=_cmd_sync)


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _confirm(prompt: str, default_yes: bool = False) -> bool:
    """Interactive y/n prompt on stderr; returns True for yes."""
    suffix = " [Y/n] " if default_yes else " [y/N] "
    sys.stderr.write(prompt + suffix)
    sys.stderr.flush()
    try:
        ans = input().strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in {"y", "yes"}


def _build_step_args(parent: argparse.Namespace, **overrides) -> argparse.Namespace:
    """Build a fresh Namespace inheriting parent's shared fields plus overrides.

    The other CLI command functions read attributes off a Namespace.  Rather
    than re-implementing each step, ``sync`` calls them directly with a
    Namespace that mimics what argparse would have produced for that step.
    """
    base = {
        "config": getattr(parent, "config", None),
        "target": getattr(parent, "target", None),
        "password": getattr(parent, "password", None),
        "store_password": getattr(parent, "store_password", False),
        "dry_run": getattr(parent, "dry_run", False),
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _cmd_sync(args: argparse.Namespace) -> int:
    """Run the configured pipeline for the selected target."""
    # Lazy imports to avoid circular import (cli.py -> commands -> cli).
    from autosieve.cli import _cmd_apply, _cmd_extract, _cmd_generate, _cmd_upload

    yes = bool(args.yes)
    pipeline: list[tuple[str, Callable[[argparse.Namespace], int], argparse.Namespace]] = []

    if not args.no_extract:
        extract_ns = _build_step_args(
            args,
            server=None,
            user=None,
            domain=None,
            folders=None,
            limit=None,
            since=None,
            headers=None,
            folder_prefix=None,
            alias_file=None,
            connection_security=None,
            no_incremental=False,
            verbose=False,
            stdout=False,
        )
        pipeline.append(("extract", _cmd_extract, extract_ns))

    generate_ns = _build_step_args(
        args,
        alias_file=None,
        output=None,
        stdout=False,
        script_name=None,
        upload=False,
        no_activate=False,
        no_check=False,
        host=None,
        username=None,
        authz_id="",
        connection_security=None,
    )
    pipeline.append(("generate", _cmd_generate, generate_ns))

    if not args.no_apply:
        apply_ns = _build_step_args(
            args,
            alias_file=None,
            folders=None,
            host=None,
            user=None,
            connection_security=None,
            no_create=False,
            subscribe=True,
        )
        pipeline.append(("apply", _cmd_apply, apply_ns))

    if not args.no_upload:
        upload_ns = _build_step_args(
            args,
            script_file=None,
            script_name=None,
            no_activate=False,
            no_check=False,
            host=None,
            username=None,
            authz_id="",
            connection_security=None,
        )
        pipeline.append(("upload", _cmd_upload, upload_ns))

    # Print the plan up front so the user sees what's about to happen.
    _eprint("=== autosieve sync plan ===")
    for name, _, _ in pipeline:
        marker = "  (dry-run)" if args.dry_run and name in {"extract", "apply", "generate"} else ""
        _eprint(f"  - {name}{marker}")
    _eprint("===========================")

    for step_name, step_fn, step_ns in pipeline:
        # Confirmation logic: 'apply' always asks unless --yes-apply.
        # Other steps are skipped only when --yes is NOT set (interactive).
        if step_name == "apply":
            if not args.yes_apply and not _confirm("apply: move messages now? (this is destructive)", default_yes=False):
                _eprint("apply: skipped by user.")
                continue
        elif not yes and not _confirm(f"{step_name}: proceed?", default_yes=True):
            _eprint(f"{step_name}: skipped by user.")
            continue

        _eprint(f"--- {step_name} ---")
        rc = step_fn(step_ns)
        if rc != 0:
            _eprint(f"sync aborted: {step_name} returned exit code {rc}.")
            return rc

    _eprint("=== sync complete ===")
    return 0
