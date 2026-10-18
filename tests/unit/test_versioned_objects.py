from backend.adapters.versioned_objects import VersionedObjectStore
from backend.eraselock.authority import InMemoryAuthority
from contracts.adapter_protocol import CheckResult, SubjectScope


def test_older_version_survives_delete_marker_but_eraselock_blocks_it():
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = VersionedObjectStore(authority)
    first = store.put(scope, "t01/p017/g4/doc", b"old content", source_version=1, operation_id="put-1")
    store.add_delete_marker_for_lab(scope, first.key)
    assert store.public_get(scope, first.key) is None
    assert store.raw_version_for_lab(first.key, first.version_id) == b"old content"
    decision = authority.close_admission(scope, "erase-1")
    committed = authority.commit_erasure(decision.decision_id)
    assert store.verify(scope, committed.committed_epoch).result == CheckResult.FAIL
    store.remediate(scope, decision.decision_id)
    assert store.verify(scope, committed.committed_epoch).result == CheckResult.PASS
    assert store.raw_version_for_lab(first.key, first.version_id) is None


def test_retained_version_reports_restricted_not_deleted():
    scope = SubjectScope("t01", "p018", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = VersionedObjectStore(authority, wall_clock=lambda: 100.0)
    version = store.put(
        scope, "t01/p018/g1/doc", b"retained", source_version=1,
        operation_id="put-1", retained_until=200.0,
        retention_authorized=True
    )
    decision = authority.close_admission(scope, "erase-2")
    committed = authority.commit_erasure(decision.decision_id)
    store.remediate(scope, decision.decision_id)
    proof = store.verify(scope, committed.committed_epoch)
    assert proof.result == CheckResult.RESTRICTED
    assert proof.retention_allowed
    assert store.public_get(scope, version.key) is None
    assert store.raw_version_for_lab(version.key, version.version_id) == b"retained"


def test_unapproved_retention_does_not_count_as_verified():
    scope = SubjectScope("t01", "p019", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = VersionedObjectStore(authority, wall_clock=lambda: 100.0)
    store.put(
        scope, "t01/p019/g1/doc", b"retained", source_version=1,
        operation_id="put-1", retained_until=200.0,
    )
    decision = authority.close_admission(scope, "erase-3")
    committed = authority.commit_erasure(decision.decision_id)
    store.remediate(scope, decision.decision_id)
    assert store.verify(scope, committed.committed_epoch).result == CheckResult.FAIL
