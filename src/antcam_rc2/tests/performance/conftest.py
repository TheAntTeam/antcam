"""Pytest options and markers for the opt-in benchmark suite.

Scoped to ``tests/performance``: the ``--benchmark`` option and the skip
logic apply only to the marker-gated tests in this directory.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--benchmark",
        action="store_true",
        default=False,
        help="run opt-in performance benchmarks (not part of the standard suite)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "benchmark: opt-in performance tests (run with --benchmark)")


def pytest_runtest_setup(item: pytest.Item) -> None:
    if "benchmark" in item.keywords and not item.config.getoption("--benchmark"):
        pytest.skip("opt-in benchmark; run with --benchmark")
