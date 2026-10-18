"""Real PostgreSQL checks; enabled when RGX_TEST_POSTGRES_DSN is set."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from backend.adapters.postgres import PostgresAdapter, migrate
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import InflightUnresolved, OperationState
from contracts.adapter_protocol import CheckResult, SubjectScope


def test_guarded_write_replay_version_and_erasure(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = PostgresAdapter(dsn, authority)
    first = store.guarded_upsert_profile(
        scope, operation_id="op-1", source_version=3, display_name="Synthetic Alpha"
    )
    assert first.outcome == "APPLIED"
    assert store.guarded_upsert_profile(
        scope, operation_id="op-1", source_version=3, display_name="Synthetic Alpha"
    ).replayed
    stale = store.guarded_upsert_profile(
        scope, operation_id="op-2", source_version=2, display_name="Old Name"
    )
    assert stale.outcome == "STALE_VERSION"
    assert store.public_profile(scope) == {"display_name": "Synthetic Alpha", "source_version": 3}
    decision = authority.close_admission(scope, "req-1")
    assert store.public_profile(scope) is None
    committed = authority.commit_erasure(decision.decision_id)
    assert store.verify(scope, committed.committed_epoch).result == CheckResult.FAIL
    authority.advance_decision(decision.decision_id, OperationState.REMEDIATING)
    store.remediate(scope, decision.decision_id)
    authority.advance_decision(decision.decision_id, OperationState.VALIDATING)
    proof = store.verify(scope, committed.committed_epoch)
    assert proof.result == CheckResult.PASS
    assert authority.verify_decision(decision.decision_id, [proof], {"rds"}).state == OperationState.VERIFIED


def test_crash_after_commit_reconciles_from_journal(database):
    _base, _schema, dsn = database
    clock = [100.0]
    scope = SubjectScope("t01", "p018", 1)
    authority = InMemoryAuthority(monotonic_clock=lambda: clock[0])
    authority.register_subject(scope)
    store = PostgresAdapter(dsn, authority)
    with pytest.raises(RuntimeError, match="Injected crash"):
        store.guarded_upsert_profile(
            scope,
            operation_id="op-crash",
            source_version=1,
            display_name="Synthetic Beta",
            crash_after_commit=True,
        )
    permit = authority.acquire_permit(scope, "op-crash")
    decision = authority.close_admission(scope, "req-2")
    clock[0] = 131
    authority.expire_permits()
    with pytest.raises(InflightUnresolved):
        authority.commit_erasure(decision.decision_id)
    assert store.reconcile_permit(permit.permit_id, "op-crash").outcome == "APPLIED"
    assert authority.commit_erasure(decision.decision_id).state == OperationState.COMMITTED


def test_old_snapshot_stays_hidden_until_reconciled(database):
    base, live_schema, live_dsn = database
    scope = SubjectScope("t01", "p019", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    live = PostgresAdapter(live_dsn, authority)
    live.guarded_upsert_profile(
        scope, operation_id="op-seed", source_version=1, display_name="Historic Profile"
    )
    candidate_schema = f"rgx_candidate_{uuid4().hex}"
    with psycopg.connect(base) as conn:
        conn.execute(f"CREATE SCHEMA {candidate_schema}")
    candidate_dsn = make_conninfo(base, options=f"-c search_path={candidate_schema}")
    try:
        migrate(candidate_dsn)
        with psycopg.connect(base) as conn:
            conn.execute(
                f"INSERT INTO {candidate_schema}.profiles SELECT * FROM {live_schema}.profiles"
            )
        decision = authority.close_admission(scope, "req-restore")
        committed = authority.commit_erasure(decision.decision_id)
        live.remediate(scope, decision.decision_id)
        candidate = PostgresAdapter(candidate_dsn, authority)
        with psycopg.connect(candidate_dsn) as conn:
            assert conn.execute("SELECT count(*) FROM profiles").fetchone()[0] == 1
        assert candidate.public_profile(scope) is None
        assert candidate.verify(scope, committed.committed_epoch).result == CheckResult.FAIL
        candidate.remediate(scope, decision.decision_id)
        assert candidate.verify(scope, committed.committed_epoch).result == CheckResult.PASS
    finally:
        with psycopg.connect(base) as conn:
            conn.execute(f"DROP SCHEMA {candidate_schema} CASCADE")
