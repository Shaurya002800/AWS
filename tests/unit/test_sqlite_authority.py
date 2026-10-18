from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.eraselock.domain import (
    AdmissionClosed, ControlState, InflightUnresolved, OperationState, StaleEpoch,
)
from backend.eraselock.sqlite_authority import SQLiteAuthority
from contracts.adapter_protocol import AdapterEvidence, CheckResult, SubjectScope


def test_authority_survives_restart_and_holds_open_permit(tmp_path):
    path = tmp_path / "outside-restore" / "authority.sqlite3"
    scope = SubjectScope("t01", "p017", 4)
    first = SQLiteAuthority(path)
    first.register_subject(scope)
    permit = first.acquire_permit(scope, "write-before-delete")
    decision = first.close_admission(scope, "erase-1")

    restarted = SQLiteAuthority(path)
    assert restarted.state(scope) == ControlState.ERASE_PENDING
    assert restarted.epoch("t01") == decision.closed_epoch
    with pytest.raises(AdmissionClosed):
        restarted.acquire_permit(scope, "late-write")
    with pytest.raises(InflightUnresolved):
        restarted.commit_erasure(decision.decision_id)

    restarted.close_permit(permit.permit_id, outcome="COMMITTED")
    committed = restarted.commit_erasure(decision.decision_id)
    assert SQLiteAuthority(path).decision(decision.decision_id) == committed
    assert committed.state == OperationState.COMMITTED


def test_authority_serializes_cross_instance_admission(tmp_path):
    path = tmp_path / "authority.sqlite3"
    scope = SubjectScope("t01", "p017", 4)
    SQLiteAuthority(path).register_subject(scope)

    def acquire(index):
        try:
            return SQLiteAuthority(path).acquire_permit(scope, f"op-{index}")
        except AdmissionClosed:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(acquire, i) for i in range(8)]
        decision = SQLiteAuthority(path).close_admission(scope, "erase-1")
        permits = [future.result() for future in futures]

    with pytest.raises(InflightUnresolved) if any(permits) else _no_exception():
        SQLiteAuthority(path).commit_erasure(decision.decision_id)
    for permit in permits:
        if permit:
            SQLiteAuthority(path).close_permit(permit.permit_id, outcome="FAILED")
    assert SQLiteAuthority(path).commit_erasure(decision.decision_id).state == OperationState.COMMITTED


def test_recovery_epoch_stays_fenced_after_restart(tmp_path):
    path = tmp_path / "authority.sqlite3"
    authority = SQLiteAuthority(path)
    old = SubjectScope("t01", "p017", 1)
    other = SubjectScope("t01", "p018", 1)
    authority.register_subject(old)
    authority.register_subject(other)
    evidence = AdapterEvidence(
        adapter="rds", scope=old, authority_epoch=0,
        result=CheckResult.PASS, trace_id="trace", inspected_count=1,
        forbidden_count=0,
    )
    authority.prepare_recovery(
        candidate_id="candidate-1", tenant_id="t01", validated_epoch=0,
        scope_digest="sha256:abc", evidence=[evidence],
        required_checks={("rds", old)},
    )
    SQLiteAuthority(path).close_admission(other, "erase-other")
    with pytest.raises(StaleEpoch):
        SQLiteAuthority(path).release_recovery("candidate-1", expected_scope_digest="sha256:abc")


from contextlib import nullcontext as _no_exception
