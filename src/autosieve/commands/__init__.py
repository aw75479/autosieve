"""Optional sub-command modules for autosieve.

Each module here is **independent** and may be deleted to remove the
corresponding feature; the only coupling point is :func:`autosieve.cli.main`,
which calls :func:`register` on each available command module to install
its argparse subparser.

Modules currently provided:

* :mod:`autosieve.commands.sync` -- end-to-end extract/generate/apply/upload.
* :mod:`autosieve.commands.backup` -- snapshot alias files + remote scripts.
* :mod:`autosieve.commands.restore` -- restore from a backup snapshot.

Adding a new sub-command: drop a new module in this package that exposes
``register(subparsers, shared)`` and have ``cli.py`` import and call it.
"""
