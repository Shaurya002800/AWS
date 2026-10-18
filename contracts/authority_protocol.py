"""Write-facing authority interface for PostgreSQL, S3, and queue workers.

An adapter should acquire a permit before a sensitive write, commit its effect
and durable operation journal together where possible, then close the permit.
If its outcome becomes uncertain, reconciliation must verify the journal before
closing the permit. A lease timeout alone is never proof that the write failed.
"""

from typing import Protocol

from backend.eraselock.domain import AdmissionPermit, ControlState
from contracts.adapter_protocol import SubjectScope


class WriteAuthority(Protocol):
    def epoch(self, tenant_id: str) -> int: ...

    def state(self, scope: SubjectScope) -> ControlState: ...

    def may_read(self, scope: SubjectScope) -> bool: ...

    def acquire_permit(
        self, scope: SubjectScope, operation_id: str, *, lease_seconds: float = 30.0
    ) -> AdmissionPermit: ...

    def close_permit(
        self, permit_id: str, *, outcome: str, journal_confirmed: bool = False
    ) -> AdmissionPermit: ...
