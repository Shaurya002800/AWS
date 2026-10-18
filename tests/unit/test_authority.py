from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import (
    AdmissionClosed,
    Conflict,
    ControlState,
    EvidenceIncomplete,
    InflightUnresolved,
    OperationState,
    PermitState,
    RecoveryState,
    StaleEpoch,
)
from contracts.adapter_protocol import AdapterEvidence, CheckResult, SubjectScope


@pytest.fixture
def scope() -> SubjectScope:
    return SubjectScope("t01", "p017", 4)


@pytest.fixture
def authority(scope: SubjectScope) -> InMemoryAuthority:
    result = InMemoryAuthority()
    result.register_subject(scope)
    return result


def evidence(scope: SubjectScope, epoch: int, *, result: CheckResult = CheckResult.PASS) -> AdapterEvidence:
    return AdapterEvidence(
        adapter="rds",
        scope=scope,
        authority_epoch=epoch,
        result=result,
        trace_id="trace-1",
        inspected_count=1,
        forbidden_count=0 if result != CheckResult.FAIL else 1,
    )


def test_close_blocks_new_admissions_and_is_idempotent(authority, scope):
    first = authority.close_admission(scope, "req-1")
    assert first == authority.close_admission(scope, "req-1")
    assert authority.state(scope) == ControlState.ERASE_PENDING
    assert not authority.may_read(scope)
    with pytest.raises(AdmissionClosed):
        authority.acquire_permit(scope, "op-after-close")
    with pytest.raises(Conflict):
        authority.close_admission(scope, "req-2")


def test_old_permit_blocks_commit_until_outcome_recorded(authority, scope):
    permit = authority.acquire_permit(scope, "op-before-close")
    decision = authority.close_admission(scope, "req-1")
    with pytest.raises(InflightUnresolved):
        authority.commit_erasure(decision.decision_id)
    authority.close_permit(permit.permit_id, outcome="COMMITTED")
    committed = authority.commit_erasure(decision.decision_id)
    assert committed.state == OperationState.COMMITTED
    assert authority.state(scope) == ControlState.ERASE_COMMITTED
    assert authority.commit_erasure(decision.decision_id) == committed


def test_expired_permit_requires_journal_reconciliation(scope):
    clock = [100.0]
    authority = InMemoryAuthority(monotonic_clock=lambda: clock[0])
    authority.register_subject(scope)
    permit = authority.acquire_permit(scope, "op-1", lease_seconds=5)
    decision = authority.close_admission(scope, "req-1")
    clock[0] = 106
    assert authority.expire_permits()[0].state == PermitState.UNCERTAIN
    with pytest.raises(InflightUnresolved):
        authority.close_permit(permit.permit_id, outcome="FAILED")
    with pytest.raises(InflightUnresolved):
        authority.commit_erasure(decision.decision_id)
    authority.close_permit(permit.permit_id, outcome="COMMITTED", journal_confirmed=True)
    assert authority.commit_erasure(decision.decision_id).state == OperationState.COMMITTED


def test_verification_requires_all_required_evidence(authority, scope):
    decision = authority.close_admission(scope, "req-1")
    committed = authority.commit_erasure(decision.decision_id)
    authority.advance_decision(decision.decision_id, OperationState.REMEDIATING)
    authority.advance_decision(decision.decision_id, OperationState.VALIDATING)
    good = evidence(scope, committed.committed_epoch)
    with pytest.raises(EvidenceIncomplete):
        authority.verify_decision(decision.decision_id, [good], {"rds", "s3"})
    with pytest.raises(EvidenceIncomplete):
        authority.verify_decision(
            decision.decision_id,
            [replace(good, result=CheckResult.NOT_VERIFIABLE)],
            {"rds"},
        )
    assert authority.verify_decision(decision.decision_id, [good], {"rds"}).state == OperationState.VERIFIED


def test_release_epoch_is_invalidated_by_new_erasure(scope):
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    other = SubjectScope("t01", "p018", 1)
    authority.register_subject(other)
    approval = authority.prepare_recovery(
        candidate_id="restore-1",
        tenant_id="t01",
        validated_epoch=0,
        scope_digest="sha256:abc",
        evidence=[evidence(scope, 0)],
        required_checks={("rds", scope)},
    )
    assert approval.state == RecoveryState.APPROVED
    authority.close_admission(other, "req-other")
    with pytest.raises(StaleEpoch):
        authority.release_recovery("restore-1", expected_scope_digest="sha256:abc")


def test_recovery_expiry_and_scope_fence(scope):
    clock = [100.0]
    authority = InMemoryAuthority(wall_clock=lambda: clock[0])
    authority.register_subject(scope)
    authority.prepare_recovery(
        candidate_id="restore-1",
        tenant_id="t01",
        validated_epoch=0,
        scope_digest="sha256:abc",
        evidence=[evidence(scope, 0)],
        required_checks={("rds", scope)},
        ttl_seconds=5,
    )
    with pytest.raises(Conflict):
        authority.release_recovery("restore-1", expected_scope_digest="sha256:wrong")
    clock[0] = 106
    with pytest.raises(StaleEpoch):
        authority.release_recovery("restore-1", expected_scope_digest="sha256:abc")


def test_recovery_requires_each_subject_adapter_pair(scope):
    authority = InMemoryAuthority()
    other = SubjectScope("t01", "p018", 1)
    authority.register_subject(scope)
    authority.register_subject(other)
    required = {("rds", scope), ("rds", other)}
    with pytest.raises(EvidenceIncomplete):
        authority.prepare_recovery(
            candidate_id="restore-incomplete",
            tenant_id="t01",
            validated_epoch=0,
            scope_digest="sha256:abc",
            evidence=[evidence(scope, 0)],
            required_checks=required,
        )
    approval = authority.prepare_recovery(
        candidate_id="restore-complete",
        tenant_id="t01",
        validated_epoch=0,
        scope_digest="sha256:abc",
        evidence=[evidence(scope, 0), evidence(other, 0)],
        required_checks=required,
    )
    assert approval.state == RecoveryState.APPROVED


def test_new_generation_requires_explicit_authorization(authority, scope):
    next_generation = SubjectScope("t01", "p017", 5)
    with pytest.raises(Conflict):
        authority.register_subject(next_generation, authorized_new_generation=True)
    decision = authority.close_admission(scope, "req-1")
    authority.commit_erasure(decision.decision_id)
    with pytest.raises(Conflict):
        authority.register_subject(next_generation)
    authority.register_subject(next_generation, authorized_new_generation=True)
    assert authority.may_read(next_generation)
    assert not authority.may_read(scope)


def test_request_and_operation_ids_cannot_cross_scopes(authority, scope):
    other = SubjectScope("t01", "p018", 1)
    authority.register_subject(other)
    authority.acquire_permit(scope, "op-1")
    with pytest.raises(Conflict):
        authority.acquire_permit(other, "op-1")
    authority.close_admission(scope, "req-1")
    with pytest.raises(Conflict):
        authority.close_admission(other, "req-1")


def test_concurrent_admission_and_erasure_are_serialized(authority, scope):
    barrier = Barrier(2)

    def write():
        barrier.wait()
        try:
            return authority.acquire_permit(scope, "racing-write")
        except AdmissionClosed:
            return None

    def erase():
        barrier.wait()
        return authority.close_admission(scope, "racing-delete")

    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future = pool.submit(write)
        delete_future = pool.submit(erase)
        permit = write_future.result()
        decision = delete_future.result()

    assert authority.state(scope) == ControlState.ERASE_PENDING
    if permit is not None:
        with pytest.raises(InflightUnresolved):
            authority.commit_erasure(decision.decision_id)
        authority.close_permit(permit.permit_id, outcome="COMMITTED")
    assert authority.commit_erasure(decision.decision_id).state == OperationState.COMMITTED


def test_required_extra_failure_cannot_be_ignored(authority, scope):
    decision = authority.close_admission(scope, "req-1")
    committed = authority.commit_erasure(decision.decision_id)
    authority.advance_decision(decision.decision_id, OperationState.REMEDIATING)
    authority.advance_decision(decision.decision_id, OperationState.VALIDATING)
    rds = evidence(scope, committed.committed_epoch)
    s3 = replace(rds, adapter="s3", result=CheckResult.FAIL, forbidden_count=1)
    with pytest.raises(EvidenceIncomplete):
        authority.verify_decision(decision.decision_id, [rds, s3], {"rds"})
