from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from backend.adapters.postgres import PostgresAdapter, migrate
from backend.adapters.search_index import PostgresSearchIndex
from backend.adapters.versioned_objects import VersionedObjectStore
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import OperationState, RecoveryState, StaleEpoch
from backend.eraselock.workflows import ErasureWorkflow, RecoveryWorkflow
from contracts.adapter_protocol import SubjectScope


def test_full_erasure_and_recovery_from_old_schema(database):
    base, live_schema, live_dsn = database
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    live = PostgresAdapter(live_dsn, authority)
    objects = VersionedObjectStore(authority)
    index = PostgresSearchIndex(live_dsn, authority, objects)
    live.guarded_upsert_profile(
        scope, operation_id="seed-profile", source_version=1, display_name="Synthetic Alpha"
    )
    version = objects.put(
        scope, "t01/p017/g4/document", b"hidden blue comet", source_version=1,
        operation_id="seed-object"
    )
    index.index_object(
        scope, key=version.key, version_id=version.version_id,
        document_id="doc-1", source_version=1, operation_id="seed-index"
    )
    candidate_schema = f"rgx_candidate_{uuid4().hex}"
    with psycopg.connect(base) as conn:
        conn.execute(f"CREATE SCHEMA {candidate_schema}")
    candidate_dsn = make_conninfo(base, options=f"-c search_path={candidate_schema}")
    try:
        migrate(candidate_dsn)
        with psycopg.connect(base) as conn:
            for table in ("profiles", "documents", "index_chunks", "lineage_edges"):
                conn.execute(
                    f"INSERT INTO {candidate_schema}.{table} "
                    f"SELECT * FROM {live_schema}.{table}"
                )
        erased = ErasureWorkflow(authority, [live, objects, index]).run(scope, "erase-1")
        assert erased.state == OperationState.VERIFIED
        assert erased.certificate.verify_integrity()
        assert erased.certificate.decision == "PASS"
        assert ErasureWorkflow(authority, [live, objects, index]).run(scope, "erase-1").state == OperationState.VERIFIED

        candidate = PostgresAdapter(candidate_dsn, authority)
        candidate_index = PostgresSearchIndex(candidate_dsn, authority, objects)
        with psycopg.connect(candidate_dsn) as conn:
            assert conn.execute("SELECT count(*) FROM profiles").fetchone()[0] == 1
        assert candidate.public_profile(scope) is None
        recovery = RecoveryWorkflow(authority, [candidate, objects, candidate_index])
        outcome = recovery.validate_candidate("restore-1", "t01", [scope])
        assert outcome.approved
        assert outcome.certificate.verify_integrity()
        assert recovery.release(outcome).state == RecoveryState.RELEASED
        with psycopg.connect(candidate_dsn) as conn:
            assert conn.execute("SELECT count(*) FROM profiles").fetchone()[0] == 0
    finally:
        with psycopg.connect(base) as conn:
            conn.execute(f"DROP SCHEMA {candidate_schema} CASCADE")


def test_approval_becomes_stale_when_new_erasure_arrives(database):
    _base, _schema, dsn = database
    first = SubjectScope("t01", "p017", 4)
    second = SubjectScope("t01", "p018", 1)
    authority = InMemoryAuthority()
    authority.register_subject(first)
    authority.register_subject(second)
    store = PostgresAdapter(dsn, authority)
    ErasureWorkflow(authority, [store]).run(first, "erase-first")
    recovery = RecoveryWorkflow(authority, [store])
    outcome = recovery.validate_candidate("restore-2", "t01", [first])
    assert outcome.approved
    authority.close_admission(second, "erase-second")
    with pytest.raises(StaleEpoch):
        recovery.release(outcome)


def test_evidence_digest_detects_change(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p019", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    cert = ErasureWorkflow(authority, [PostgresAdapter(dsn, authority)]).run(
        scope, "erase-one"
    ).certificate
    assert cert.verify_integrity()
    assert not replace(cert, decision="HOLD").verify_integrity()
