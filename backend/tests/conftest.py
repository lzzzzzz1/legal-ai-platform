"""Cross-platform pytest configuration for the backend suite."""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Avoid Windows' shared temp directory and stale test-run locks.

    Some managed Windows environments deny access to the shared
    ``%LOCALAPPDATA%\\Temp\\pytest-*`` directory.  A fixed in-repository
    ``--basetemp`` also fails after an interrupted run because an indexer or a
    running process can retain a handle.  A project-local, process-unique
    directory is both writable and isolated from earlier runs.
    """

    runtime_root = Path(config.rootpath) / ".pytest-runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = runtime_root / f"run-{os.getpid()}"
