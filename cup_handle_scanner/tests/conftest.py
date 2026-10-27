"""Shared fixtures. Synthetic data lives in scanner/synthetic.py.

Tests write only to pytest's tmp_path; a session guard fails the run if anything under output/
was created, changed or removed while the tests ran.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanner.config import load_config  # noqa: E402

SEEDS = list(range(20))  # every synthetic detector test runs over these fixed seeds


@pytest.fixture
def cfg():
    return load_config()


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in folder.rglob("*") if p.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may reach Yahoo or Wikipedia; a test that needs either replaces it with a fake."""
    from scanner import data, universe

    def blocked(*_args, **_kwargs):
        raise AssertionError("network access in a test")
    monkeypatch.setattr(data, "yf_download", blocked)
    monkeypatch.setattr(universe.requests, "get", blocked)


@pytest.fixture(scope="session", autouse=True)
def output_folder_untouched():
    out = ROOT / load_config()["report"]["output_dir"]
    before = _snapshot(out)
    yield
    after = _snapshot(out)
    assert after == before, "tests must not write to output/ (use tmp_path)"
