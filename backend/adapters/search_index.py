"""Deterministic PostgreSQL retrieval index with EraseLock ingestion checks."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import psycopg

from backend.adapters.versioned_objects import VersionedObjectStore
from backend.eraselock.domain import AdmissionClosed, Conflict
from contracts.adapter_protocol import AdapterEvidence, CheckResult, Location, SubjectScope
from contracts.authority_protocol import WriteAuthority


@dataclass(frozen=True, slots=True)
class SearchHit:
    document_id: str
    chunk_id: str
    text: str


class PostgresSearchIndex:
    name = "index"

    def __init__(self, dsn: str, authority: WriteAuthority, objects: VersionedObjectStore) -> None:
        self.dsn = dsn
        self.authority = authority
        self.objects = objects

    def index_object(
        self,
        scope: SubjectScope,
        *,
        key: str,
        version_id: str,
        document_id: str,
        source_version: int,
        operation_id: str,
    ) -> str:
        permit = self.authority.acquire_permit(scope, operation_id)
        content = self.objects.read_version_for_ingestion(scope, key, version_id)
        if content is None:
            self.authority.close_permit(permit.permit_id, outcome="FAILED")
            raise AdmissionClosed("Source is unavailable or generation is forbidden")
        text = content.decode("utf-8")
        chunk_id = sha256(f"{document_id}:{source_version}:{text}".encode()).hexdigest()[:24]
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))
                prior = conn.execute(
                    """SELECT tenant_id, subject_id, generation, source_version
                       FROM operation_journal WHERE operation_id=%s""",
                    (operation_id,),
                ).fetchone()
                if prior is not None:
                    if prior != (
                        scope.tenant_id, scope.subject_id, scope.subject_generation, source_version
                    ):
                        raise Conflict("Index operation ID reused with different source")
                else:
                    existing_doc = conn.execute(
                        """SELECT tenant_id, subject_id, generation, source_version
                           FROM documents WHERE document_id=%s FOR UPDATE""",
                        (document_id,),
                    ).fetchone()
                    if existing_doc is not None:
                        if existing_doc[:3] != (
                            scope.tenant_id, scope.subject_id, scope.subject_generation
                        ):
                            raise Conflict("Document ID belongs to another subject")
                        if existing_doc[3] >= source_version:
                            raise Conflict("Document source version is stale")
                    conn.execute(
                        """INSERT INTO documents
                             (document_id, tenant_id, subject_id, generation,
                              source_version, object_key)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (document_id) DO UPDATE
                             SET source_version=EXCLUDED.source_version,
                                 object_key=EXCLUDED.object_key,
                                 status='ACTIVE'
                           WHERE EXCLUDED.source_version > documents.source_version""",
                        (
                            document_id, scope.tenant_id, scope.subject_id,
                            scope.subject_generation, source_version, key,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO index_chunks
                             (chunk_id, document_id, tenant_id, subject_id, generation,
                              source_version, chunk_text)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (chunk_id) DO NOTHING""",
                        (
                            chunk_id, document_id, scope.tenant_id, scope.subject_id,
                            scope.subject_generation, source_version, text,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO lineage_edges
                             (tenant_id, subject_id, generation, source_type, source_id,
                              target_type, target_id)
                           VALUES (%s, %s, %s, 'S3_VERSION', %s, 'INDEX_CHUNK', %s)
                           ON CONFLICT DO NOTHING""",
                        (
                            scope.tenant_id, scope.subject_id, scope.subject_generation,
                            version_id, chunk_id,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO operation_journal
                             (operation_id, tenant_id, subject_id, generation,
                              authority_epoch, source_version, outcome)
                           VALUES (%s, %s, %s, %s, %s, %s, 'APPLIED')""",
                        (
                            operation_id, scope.tenant_id, scope.subject_id,
                            scope.subject_generation, permit.fence_epoch, source_version,
                        ),
                    )
        self.authority.close_permit(permit.permit_id, outcome="COMMITTED")
        return chunk_id

    def search(self, tenant_id: str, query: str) -> list[SearchHit]:
        if not query:
            return []
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """SELECT document_id, chunk_id, chunk_text, subject_id, generation
                   FROM index_chunks
                   WHERE tenant_id=%s AND status='ACTIVE' AND chunk_text ILIKE %s
                   ORDER BY chunk_id""",
                (tenant_id, f"%{query}%"),
            ).fetchall()
        return [
            SearchHit(row[0], row[1], row[2]) for row in rows
            if self.authority.may_read(SubjectScope(tenant_id, row[3], row[4]))
        ]

    def discover(self, scope: SubjectScope) -> list[Location]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """SELECT chunk_id, source_version FROM index_chunks
                   WHERE tenant_id=%s AND subject_id=%s AND generation=%s""",
                (scope.tenant_id, scope.subject_id, scope.subject_generation),
            ).fetchall()
        return [Location(self.name, row[0], str(row[1])) for row in rows]

    def remediate(self, scope: SubjectScope, decision_id: str) -> None:
        if not decision_id:
            raise ValueError("Decision ID is required")
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """DELETE FROM index_chunks
                   WHERE tenant_id=%s AND subject_id=%s AND generation=%s""",
                (scope.tenant_id, scope.subject_id, scope.subject_generation),
            )

    def verify(self, scope: SubjectScope, authority_epoch: int) -> AdapterEvidence:
        with psycopg.connect(self.dsn) as conn:
            count = conn.execute(
                """SELECT count(*) FROM index_chunks
                   WHERE tenant_id=%s AND subject_id=%s AND generation=%s
                     AND status='ACTIVE'""",
                (scope.tenant_id, scope.subject_id, scope.subject_generation),
            ).fetchone()[0]
        return AdapterEvidence(
            adapter=self.name,
            scope=scope,
            authority_epoch=authority_epoch,
            result=CheckResult.PASS if count == 0 else CheckResult.FAIL,
            trace_id=f"verify-index-{scope.tenant_id}-{scope.subject_id}-{authority_epoch}",
            inspected_count=count,
            forbidden_count=count,
        )
