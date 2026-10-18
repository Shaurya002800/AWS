import os

import pytest

from lab.suite import run_suite


def test_full_suite_reports_explicit_denominators():
    dsn = os.getenv("RGX_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set RGX_TEST_POSTGRES_DSN for PostgreSQL integration checks")
    report = run_suite(dsn, seeds=(42,))
    assert report.pairs == 4
    assert report.baseline_attempts == report.protected_attempts == 4
    assert report.baseline_exposures == 4
    assert report.protected_exposures == 0
    assert report.all_pairs_improved
    assert report.result == "PASS"
