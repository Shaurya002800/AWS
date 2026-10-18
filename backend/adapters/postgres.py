"""PostgreSQL operational adapter and guarded profile writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import psycopg

from backend.eraselock.domain import Conflict, InflightUnresolved
from contracts.adapter_protocol import AdapterEvidence, CheckResult, Location, SubjectScope
from contracts.authority_protocol import WriteAuthority


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "001_initial.sql"


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    operation_id: str
    outcome: str
    source_version: int
    replayed: bool = False


def migrate(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(MIGRATION.read_text())


class PostgresAdapter:
    name = "rds"

    def __init__(self, dsn: str, authority: WriteAuthority) -> None:
        self.dsn = dsn
        self.authority = authority

    def guarded_upsert_profile(
        self,
        scope: SubjectScope,
        *,
        operation_id: str,
        source_version: int,
        display_name: str,
        crash_after_commit: bool = False,
    ) -> WriteOutcome:
        if source_version < 1 or not display_name or not operation_id:
            raise ValueError("Operation, source version, and display name are required")
        permit = self.authority.acquire_permit(scope, operation_id)
        try:
            with psycopg.connect(self.dsn) as conn:
                with conn.transaction():
                    # Serializes duplicate operation IDs within PostgreSQL. The
                    # authority permit separately serializes admission/closure.
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))
                    prior = conn.execute(
                        """SELECT tenant_id, subject_id, generation, source_version, outcome
                           FROM operation_journal WHERE operation_id = %s""",
                        (operation_id,),
                    ).fetchone()
                    if prior is not None:
                        if prior[:4] != (
                            scope.tenant_id,
                            scope.subject_id,
                            scope.subject_generation,
                            source_version,
                        ):
                            raise Conflict("Operation ID reused with a different payload")
                        result = WriteOutcome(operation_id, prior[4], source_version, replayed=True)
                    else:
                        applied = conn.execute(
                            """INSERT INTO profiles
                                 (tenant_id, subject_id, generation, source_version, display_name)
                               VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (tenant_id, subject_id, generation)
                               DO UPDATE SET source_version = EXCLUDED.source_version,
                                             display_name = EXCLUDED.display_name,
                                             status = 'ACTIVE'
                               WHERE EXCLUDED.source_version > profiles.source_version
                               RETURNING source_version""",
                            (
                                scope.tenant_id,
                                scope.subject_id,
                                scope.subject_generation,
                                source_version,
                                display_name,
                            ),
                        ).fetchone()
                        outcome = "APPLIED" if applied is not None else "STALE_VERSION"
                        conn.execute(
                            """INSERT INTO operation_journal
                                 (operation_id, tenant_id, subject_id, generation,
                                  authority_epoch, source_version, outcome)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (
                                operation_id,
                                scope.tenant_id,
                                scope.subject_id,
                                scope.subject_generation,
                                permit.fence_epoch,
                                source_version,
                                outcome,
                            ),
                        )
                        result = WriteOutcome(operation_id, outcome, source_version)
            if crash_after_commit:
                raise RuntimeError("Injected crash after PostgreSQL commit")
            self.authority.close_permit(permit.permit_id, outcome="COMMITTED")
            return result
        except Exception:
            # If a database outcome is uncertain, never clear the permit here.
            # A reconciler will inspect the durable journal after lease expiry.
            raise

    def reconcile_permit(self, permit_id: str, operation_id: str) -> WriteOutcome:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """SELECT source_version, outcome FROM operation_journal
                   WHERE operation_id = %s""",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise InflightUnresolved("No durable journal outcome; retain permit as uncertain")
        self.authority.close_permit(
            permit_id, outcome="COMMITTED", journal_confirmed=True
        )
        return WriteOutcome(operation_id, row[1], row[0], replayed=True)

    def public_profile(self, scope: SubjectScope) -> dict[str, object] | None:
        if not self.authority.may_read(scope):
            return None
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """SELECT display_name, source_version FROM profiles
                   WHERE tenant_id=%s AND subject_id=%s AND generation=%s
                     AND status='ACTIVE'""",
                (scope.tenant_id, scope.subject_id, scope.subject_generation),
            ).fetchone()
        if row is None:
            return None
        if not self.authority.may_read(scope):
            return None
        return {"display_name": row[0], "source_version": row[1]}

    def discover(self, scope: SubjectScope) -> list[Location]:
        locations: list[Location] = []
        with psycopg.connect(self.dsn) as conn:
            for table, id_column in (
                ("profiles", "subject_id"),
                ("documents", "document_id"),
                ("index_chunks", "chunk_id"),
            ):
                rows = conn.execute(
                    f"SELECT {id_column}, source_version FROM {table} "
                    "WHERE tenant_id=%s AND subject_id=%s AND generation=%s",
                    (scope.tenant_id, scope.subject_id, scope.subject_generation),
                ).fetchall()
                locations.extend(
                    Location(self.name, f"{table}:{row[0]}", str(row[1])) for row in rows
                )
        return locations

    def remediate(self, scope: SubjectScope, decision_id: str) -> None:
        if not decision_id:
            raise ValueError("Decision ID is required")
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                for table in ("index_chunks", "documents", "profiles"):
                    conn.execute(
                        f"DELETE FROM {table} "
                        "WHERE tenant_id=%s AND subject_id=%s AND generation=%s",
                        (scope.tenant_id, scope.subject_id, scope.subject_generation),
                    )

    def verify(self, scope: SubjectScope, authority_epoch: int) -> AdapterEvidence:
        counts = []
        with psycopg.connect(self.dsn) as conn:
            for table in ("profiles", "documents", "index_chunks"):
                counts.append(
                    conn.execute(
                        f"SELECT count(*) FROM {table} "
                        "WHERE tenant_id=%s AND subject_id=%s AND generation=%s "
                        "AND status='ACTIVE'",
                        (scope.tenant_id, scope.subject_id, scope.subject_generation),
                    ).fetchone()[0]
                )
        forbidden = sum(counts)
        return AdapterEvidence(
            adapter=self.name,
            scope=scope,
            authority_epoch=authority_epoch,
            result=CheckResult.PASS if forbidden == 0 else CheckResult.FAIL,
            trace_id=f"verify-rds-{scope.tenant_id}-{scope.subject_id}-{authority_epoch}",
            inspected_count=forbidden,
            forbidden_count=forbidden,
        )
