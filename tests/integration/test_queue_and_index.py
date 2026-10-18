import pytest

from backend.adapters.postgres import PostgresAdapter
from backend.adapters.search_index import PostgresSearchIndex
from backend.adapters.versioned_objects import VersionedObjectStore
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import AdmissionClosed
from backend.workers.local_queue import LocalProfileQueue, MessageResult, ProfileEvent
from contracts.adapter_protocol import SubjectScope


def test_stale_queue_message_cannot_recreate_erased_profile(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = PostgresAdapter(dsn, authority)
    queue = LocalProfileQueue(store)
    queue.send(ProfileEvent("event-old", scope, 1, "Old Name"))
    decision = authority.close_admission(scope, "erase-1")
    authority.commit_erasure(decision.decision_id)
    assert queue.consume_one() == MessageResult.REJECTED_ERASED
    assert store.public_profile(scope) is None
    assert len(queue.dead_letter) == 1


def test_duplicate_queue_event_has_one_effect(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p018", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = PostgresAdapter(dsn, authority)
    queue = LocalProfileQueue(store)
    for _ in range(20):
        queue.send(ProfileEvent("event-dup", scope, 1, "Synthetic"))
    outcomes = [queue.consume_one() for _ in range(20)]
    assert outcomes.count(MessageResult.APPLIED) == 1
    assert outcomes.count(MessageResult.DUPLICATE) == 19
    assert store.public_profile(scope)["source_version"] == 1


def test_index_reingestion_is_blocked_after_erasure(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p019", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    objects = VersionedObjectStore(authority)
    version = objects.put(
        scope, "t01/p019/g1/doc", b"private blue comet", source_version=1,
        operation_id="put-1"
    )
    index = PostgresSearchIndex(dsn, authority, objects)
    index.index_object(
        scope, key=version.key, version_id=version.version_id,
        document_id="doc-1", source_version=1, operation_id="index-1"
    )
    assert len(index.search("t01", "blue comet")) == 1
    decision = authority.close_admission(scope, "erase-1")
    committed = authority.commit_erasure(decision.decision_id)
    assert index.search("t01", "blue comet") == []
    with pytest.raises(AdmissionClosed):
        index.index_object(
            scope, key=version.key, version_id=version.version_id,
            document_id="doc-1", source_version=2, operation_id="index-stale"
        )
    index.remediate(scope, decision.decision_id)
    assert index.verify(scope, committed.committed_epoch).forbidden_count == 0
