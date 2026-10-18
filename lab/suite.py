"""Aggregate paired experiment results without overstating coverage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .runner import SCENARIOS, LabResult, run_scenario


@dataclass(frozen=True, slots=True)
class SuiteReport:
    report_version: str
    generated_at: str
    seeds: tuple[int, ...]
    scenarios: tuple[str, ...]
    pairs: int
    baseline_attempts: int
    baseline_exposures: int
    protected_attempts: int
    protected_exposures: int
    all_pairs_improved: bool
    all_protected_prevented: bool
    result: str
    scope_note: str
    runs: tuple[dict[str, object], ...]


def run_suite(dsn: str, *, seeds: tuple[int, ...] = (42,)) -> SuiteReport:
    if not seeds or any(seed < 0 or seed > 999999 for seed in seeds):
        raise ValueError("Supply one or more synthetic seeds from 0 to 999999")
    runs: list[LabResult] = []
    pairs: list[tuple[LabResult, LabResult]] = []
    for seed in seeds:
        for scenario in SCENARIOS:
            baseline = run_scenario(dsn, scenario, "baseline", seed=seed)
            protected = run_scenario(dsn, scenario, "protected", seed=seed)
            runs.extend((baseline, protected))
            pairs.append((baseline, protected))
    baseline_attempts = sum(base.forbidden_attempts for base, _ in pairs)
    baseline_exposures = sum(base.forbidden_exposures for base, _ in pairs)
    protected_attempts = sum(protected.forbidden_attempts for _, protected in pairs)
    protected_exposures = sum(protected.forbidden_exposures for _, protected in pairs)
    improved = all(
        base.forbidden_exposures > protected.forbidden_exposures
        for base, protected in pairs
    )
    prevented = all(protected.forbidden_exposures == 0 for _, protected in pairs)
    return SuiteReport(
        report_version="1.0",
        generated_at=datetime.now(timezone.utc).isoformat(),
        seeds=seeds,
        scenarios=SCENARIOS,
        pairs=len(pairs),
        baseline_attempts=baseline_attempts,
        baseline_exposures=baseline_exposures,
        protected_attempts=protected_attempts,
        protected_exposures=protected_exposures,
        all_pairs_improved=improved,
        all_protected_prevented=prevented,
        result="PASS" if improved and prevented else "FAIL",
        scope_note=(
            "Synthetic local PostgreSQL, in-memory object versions and queue; "
            "one forbidden attempt per pair. No AWS deployment or general erasure guarantee."
        ),
        runs=tuple(asdict(run) for run in runs),
    )
