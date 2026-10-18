"""Paired local experiments with an independent forbidden-exposure oracle."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

from backend.adapters.postgres import PostgresAdapter, migrate
from backend.adapters.search_index import PostgresSearchIndex
from backend.adapters.versioned_objects import VersionedObjectStore
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import AdmissionClosed
from backend.eraselock.workflows import ErasureWorkflow, RecoveryWorkflow
from backend.workers.local_queue import LocalProfileQueue, MessageResult, ProfileEvent
from contracts.adapter_protocol import SubjectScope


SCENARIOS = (
    "old_database_recovery",
    "delayed_message_after_erasure",
    "s3_version_resurrection",
    "index_reingestion",
)


@dataclass(frozen=True, slots=True)
class LabResult:
    scenario: str
    mode: str
    seed: int
    forbidden_attempts: int
    forbidden_exposures: int
    prevented: bool
    evidence_decision: str
    evidence_integrity: str | None
    git_revision: str
    generated_at: str
    notes: tuple[str, ...]


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def run_scenario(dsn: str, scenario: str, mode: str, *, seed: int = 42) -> LabResult:
    if scenario not in SCENARIOS or mode not in ("baseline", "protected"):
        raise ValueError("Unknown scenario or mode")
    schema = f"rgx_lab_{uuid4().hex}"
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    local_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
    try:
        migrate(local_dsn)
        exposure, decision, digest, notes = _execute(local_dsn, dsn, schema, scenario, mode, seed)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
    return LabResult(
        scenario=scenario,
        mode=mode,
        seed=seed,
        forbidden_attempts=1,
        forbidden_exposures=int(exposure),
        prevented=not exposure,
        evidence_decision=decision,
        evidence_integrity=digest,
        git_revision=_git_revision(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        notes=tuple(notes),
    )


def _execute(local_dsn, base_dsn, schema, scenario, mode, seed):
    scope = SubjectScope("t01", f"p{seed:03d}", 1)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    database = PostgresAdapter(local_dsn, authority)
    objects = VersionedObjectStore(authority)
    index = PostgresSearchIndex(local_dsn, authority, objects)
    database.guarded_upsert_profile(
        scope, operation_id="seed-profile", source_version=1,
        display_name=f"Synthetic Subject {seed}"
    )
    evidence_decision = "NOT_RUN"
    evidence_integrity = None
    notes = []

    if scenario == "old_database_recovery":
        candidate_schema = f"rgx_restore_{uuid4().hex}"
        with psycopg.connect(base_dsn) as conn:
            conn.execute(f"CREATE SCHEMA {candidate_schema}")
        candidate_dsn = make_conninfo(base_dsn, options=f"-c search_path={candidate_schema}")
        try:
            migrate(candidate_dsn)
            with psycopg.connect(base_dsn) as conn:
                conn.execute(
                    f"INSERT INTO {candidate_schema}.profiles SELECT * FROM {schema}.profiles"
                )
            erasure = ErasureWorkflow(authority, [database]).run(scope, "erase-1")
            evidence_decision = erasure.certificate.decision
            evidence_integrity = erasure.certificate.integrity
            candidate = PostgresAdapter(candidate_dsn, authority)
            if mode == "protected":
                recovery = RecoveryWorkflow(authority, [candidate])
                outcome = recovery.validate_candidate("candidate-1", "t01", [scope])
                if not outcome.approved:
                    notes.append("Candidate held by release gate")
                else:
                    recovery.release(outcome)
            with psycopg.connect(candidate_dsn) as conn:
                # Oracle queries the would-be published candidate independently.
                exposed = conn.execute(
                    "SELECT count(*) FROM profiles WHERE tenant_id=%s AND subject_id=%s",
                    (scope.tenant_id, scope.subject_id),
                ).fetchone()[0] > 0
            notes.append("Baseline routes raw old candidate; treatment remediates before release")
            return exposed, evidence_decision, evidence_integrity, notes
        finally:
            with psycopg.connect(base_dsn) as conn:
                conn.execute(f"DROP SCHEMA {candidate_schema} CASCADE")

    if scenario == "delayed_message_after_erasure":
        queue = LocalProfileQueue(database)
        queue.send(ProfileEvent("event-old", scope, 2, "Reintroduced"))
        erasure = ErasureWorkflow(authority, [database]).run(scope, "erase-1")
        evidence_decision = erasure.certificate.decision
        evidence_integrity = erasure.certificate.integrity
        if mode == "baseline":
            with psycopg.connect(local_dsn) as conn:
                conn.execute(
                    """INSERT INTO profiles
                       (tenant_id, subject_id, generation, source_version, display_name)
                       VALUES (%s, %s, %s, 2, 'Reintroduced')""",
                    (scope.tenant_id, scope.subject_id, scope.subject_generation),
                )
            notes.append("Baseline bypasses admission like an unguarded consumer")
        else:
            assert queue.consume_one() == MessageResult.REJECTED_ERASED
            notes.append("Protected queue rejects stale event")
        with psycopg.connect(local_dsn) as conn:
            exposed = conn.execute(
                "SELECT count(*) FROM profiles WHERE tenant_id=%s AND subject_id=%s",
                (scope.tenant_id, scope.subject_id),
            ).fetchone()[0] > 0
        return exposed, evidence_decision, evidence_integrity, notes

    version = objects.put(
        scope, f"t01/{scope.subject_id}/g1/doc", b"private blue comet",
        source_version=1, operation_id="seed-object"
    )
    if scenario == "s3_version_resurrection":
        objects.add_delete_marker_for_lab(scope, version.key)
        adapters = [database] if mode == "baseline" else [database, objects]
        erasure = ErasureWorkflow(authority, adapters).run(scope, "erase-1")
        evidence_decision = erasure.certificate.decision
        evidence_integrity = erasure.certificate.integrity
        if mode == "baseline":
            objects.remove_latest_delete_marker_for_lab(scope, version.key)
            exposed = objects.raw_version_for_lab(version.key, version.version_id) is not None
            notes.append("Baseline removes delete marker and serves older version without policy check")
        else:
            exposed = objects.raw_version_for_lab(version.key, version.version_id) is not None
            notes.append("Treatment enumerates and removes known object versions")
        return exposed, evidence_decision, evidence_integrity, notes

    if scenario == "index_reingestion":
        erasure = ErasureWorkflow(authority, [database, objects, index]).run(scope, "erase-1")
        evidence_decision = erasure.certificate.decision
        evidence_integrity = erasure.certificate.integrity
        if mode == "baseline":
            with psycopg.connect(local_dsn) as conn:
                conn.execute(
                    """INSERT INTO index_chunks
                       (chunk_id, document_id, tenant_id, subject_id, generation,
                        source_version, chunk_text)
                       VALUES ('stale-chunk', 'doc-1', %s, %s, %s, 1, 'private blue comet')""",
                    (scope.tenant_id, scope.subject_id, scope.subject_generation),
                )
            notes.append("Baseline publishes a stale derived chunk")
        else:
            try:
                index.index_object(
                    scope, key=version.key, version_id=version.version_id,
                    document_id="doc-1", source_version=1, operation_id="stale-index"
                )
            except AdmissionClosed:
                notes.append("Protected index admission rejected stale generation")
        with psycopg.connect(local_dsn) as conn:
            exposed = conn.execute(
                "SELECT count(*) FROM index_chunks WHERE chunk_text ILIKE '%blue comet%'"
            ).fetchone()[0] > 0
        return exposed, evidence_decision, evidence_integrity, notes

    raise AssertionError("Scenario dispatch failed")
