"""In-memory S3 versioning model for reproducible offline experiments.

This is not an S3 client. It models delete markers, older versions, and
retention so the lab can test policy behavior before a cloud adapter exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import time
from typing import Callable
from uuid import uuid4

from backend.eraselock.domain import Conflict
from contracts.adapter_protocol import AdapterEvidence, CheckResult, Location, SubjectScope
from contracts.authority_protocol import WriteAuthority


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    version_id: str
    scope: SubjectScope
    key: str
    source_version: int
    content: bytes
    delete_marker: bool = False
    retained_until: float | None = None
    retention_authorized: bool = False


class VersionedObjectStore:
    name = "s3"

    def __init__(self, authority: WriteAuthority, wall_clock: Callable[[], float] = time) -> None:
        self.authority = authority
        self._now = wall_clock
        self._lock = RLock()
        self._objects: dict[str, list[ObjectVersion]] = {}
        self._operations: dict[str, ObjectVersion] = {}

    def put(
        self,
        scope: SubjectScope,
        key: str,
        content: bytes,
        *,
        source_version: int,
        operation_id: str,
        retained_until: float | None = None,
        retention_authorized: bool = False,
    ) -> ObjectVersion:
        if not key or not operation_id or source_version < 1:
            raise ValueError("Key, operation ID, and positive version are required")
        permit = self.authority.acquire_permit(scope, operation_id)
        with self._lock:
            prior = self._operations.get(operation_id)
            if prior is not None:
                if (
                    prior.scope, prior.key, prior.source_version, prior.content,
                    prior.retained_until, prior.retention_authorized,
                ) != (
                    scope, key, source_version, content,
                    retained_until, retention_authorized,
                ):
                    raise Conflict("Operation ID reused with different object payload")
                self.authority.close_permit(permit.permit_id, outcome="COMMITTED")
                return prior
            versions = self._objects.setdefault(key, [])
            if any(item.scope != scope for item in versions):
                raise Conflict("Object key is already owned by another subject generation")
            if any(not item.delete_marker and item.source_version >= source_version for item in versions):
                raise Conflict("Source version is stale")
            version = ObjectVersion(
                version_id=str(uuid4()),
                scope=scope,
                key=key,
                source_version=source_version,
                content=content,
                retained_until=retained_until,
                retention_authorized=retention_authorized,
            )
            versions.append(version)
            self._operations[operation_id] = version
        self.authority.close_permit(permit.permit_id, outcome="COMMITTED")
        return version

    def add_delete_marker_for_lab(self, scope: SubjectScope, key: str) -> ObjectVersion:
        """Simulate an ordinary S3 DELETE, without claiming physical erasure."""
        with self._lock:
            versions = self._objects.get(key)
            if not versions or any(item.scope != scope for item in versions):
                raise Conflict("Unknown object or wrong subject")
            marker = ObjectVersion(str(uuid4()), scope, key, versions[-1].source_version, b"", True)
            versions.append(marker)
            return marker

    def remove_latest_delete_marker_for_lab(self, scope: SubjectScope, key: str) -> None:
        """Simulate an unsafe restore of an older visible object version."""
        with self._lock:
            versions = self._objects.get(key)
            if not versions or versions[-1].scope != scope or not versions[-1].delete_marker:
                raise Conflict("Latest version is not a matching delete marker")
            versions.pop()

    def raw_version_for_lab(self, key: str, version_id: str) -> bytes | None:
        """Internal lab inspection, never a public retrieval path."""
        with self._lock:
            for item in self._objects.get(key, []):
                if item.version_id == version_id and not item.delete_marker:
                    return item.content
        return None

    def read_version_for_ingestion(
        self, scope: SubjectScope, key: str, version_id: str
    ) -> bytes | None:
        if not self.authority.may_read(scope):
            return None
        with self._lock:
            versions = self._objects.get(key, [])
            if not versions or versions[-1].delete_marker:
                return None
            for item in versions:
                if item.scope == scope and item.version_id == version_id and not item.delete_marker:
                    return item.content if self.authority.may_read(scope) else None
        return None

    def public_get(self, scope: SubjectScope, key: str) -> bytes | None:
        if not self.authority.may_read(scope):
            return None
        with self._lock:
            versions = self._objects.get(key, [])
            if not versions or versions[-1].delete_marker or versions[-1].scope != scope:
                return None
            return versions[-1].content if self.authority.may_read(scope) else None

    def discover(self, scope: SubjectScope) -> list[Location]:
        with self._lock:
            return [
                Location(self.name, f"{key}:{item.version_id}", str(item.source_version))
                for key, versions in self._objects.items()
                for item in versions
                if item.scope == scope and not item.delete_marker
            ]

    def remediate(self, scope: SubjectScope, decision_id: str) -> None:
        if not decision_id:
            raise ValueError("Decision ID is required")
        with self._lock:
            for key, versions in list(self._objects.items()):
                remaining = [
                    item for item in versions
                    if item.scope != scope
                    or (item.retained_until is not None and item.retained_until > self._now())
                ]
                if remaining:
                    self._objects[key] = remaining
                else:
                    del self._objects[key]

    def verify(self, scope: SubjectScope, authority_epoch: int) -> AdapterEvidence:
        with self._lock:
            remaining = [
                item for versions in self._objects.values() for item in versions
                if item.scope == scope and not item.delete_marker
            ]
        retained = [
            item for item in remaining
            if item.retained_until is not None and item.retained_until > self._now()
        ]
        if not remaining:
            result = CheckResult.PASS
        elif (
            len(retained) == len(remaining)
            and all(item.retention_authorized for item in retained)
            and not self.authority.may_read(scope)
        ):
            result = CheckResult.RESTRICTED
        else:
            result = CheckResult.FAIL
        return AdapterEvidence(
            adapter=self.name,
            scope=scope,
            authority_epoch=authority_epoch,
            result=result,
            trace_id=f"verify-s3-{scope.tenant_id}-{scope.subject_id}-{authority_epoch}",
            inspected_count=len(remaining),
            forbidden_count=len(remaining) - len(retained),
            retention_allowed=result == CheckResult.RESTRICTED,
            uncertainty=None if result != CheckResult.FAIL else "Unremediated object version",
        )
