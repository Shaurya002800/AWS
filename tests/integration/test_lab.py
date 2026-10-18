import pytest

from lab.runner import SCENARIOS, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_paired_lab_run_exposes_baseline_and_prevents_protected(database, scenario):
    base, _schema, _dsn = database
    baseline = run_scenario(base, scenario, "baseline", seed=42)
    protected = run_scenario(base, scenario, "protected", seed=42)
    assert baseline.forbidden_attempts == protected.forbidden_attempts == 1
    assert baseline.forbidden_exposures == 1
    assert protected.forbidden_exposures == 0
    assert protected.prevented
    assert protected.evidence_integrity.startswith("sha256:")
