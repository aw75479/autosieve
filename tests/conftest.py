"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def sample_config_path() -> Path:
    return SAMPLES_DIR / "sieve_alias_mapping.sample.json"


@pytest.fixture
def sample_sieve_path() -> Path:
    return SAMPLES_DIR / "generated.sample.sieve"
