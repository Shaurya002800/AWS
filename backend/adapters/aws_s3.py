"""Version-aware S3 adapter with explicit mutation opt-in.

No AWS call occurs at import. Construct this adapter only after the account
gate, with an injected boto3 S3 client and a private versioned bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.adapter_protocol import AdapterEvidence, CheckResult, Location, SubjectScope
from contracts.authority_protocol import WriteAuthority


@dataclass(frozen=True, slots=True)
class S3VersionRef:
    key: str
    version_id: str
    delete_marker: bool


class AwsS3Adapter:
    name = "s3"

    def __init__(
        self,
        client: Any,
        bucket: str,
        authority: WriteAuthority,
        *,
        allow_mutation: bool = False,
        authorized_retained_versions: set[tuple[str, str]] | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("Bucket name is required")
        self.client = client
        self.bucket = bucket
        self.authority = authority
        self.allow_mutation = allow_mutation
        self.authorized_retained_versions = authorized_retained_versions or set()

    @staticmethod
    def prefix(scope: SubjectScope) -> str:
        if not scope.tenant_id.isidentifier() or not scope.subject_id.isidentifier():
            raise ValueError("Tenant and subject IDs must be safe prefix components")
        return f"{scope.tenant_id}/{scope.subject_id}/g{scope.subject_generation}/"

    def _versions(self, scope: SubjectScope) -> list[S3VersionRef]:
        prefix = self.prefix(scope)
        paginator = self.client.get_paginator("list_object_versions")
        found = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Versions", []):
                if item["Key"].startswith(prefix):
                    found.append(S3VersionRef(item["Key"], item["VersionId"], False))
            for item in page.get("DeleteMarkers", []):
                if item["Key"].startswith(prefix):
                    found.append(S3VersionRef(item["Key"], item["VersionId"], True))
        return found

    def discover(self, scope: SubjectScope) -> list[Location]:
        return [
            Location(self.name, f"{item.key}:{item.version_id}")
            for item in self._versions(scope) if not item.delete_marker
        ]

    def remediate(self, scope: SubjectScope, decision_id: str) -> None:
        if not self.allow_mutation:
            raise RuntimeError("S3 mutation is disabled; account gate is not complete")
        if not decision_id:
            raise ValueError("Decision ID is required")
        for item in self._versions(scope):
            if (item.key, item.version_id) in self.authorized_retained_versions:
                continue
            # A specific VersionId is required. A plain DELETE would only add
            # a marker and leave older bytes behind in a versioned bucket.
            self.client.delete_object(
                Bucket=self.bucket, Key=item.key, VersionId=item.version_id
            )

    def verify(self, scope: SubjectScope, authority_epoch: int) -> AdapterEvidence:
        remaining = [item for item in self._versions(scope) if not item.delete_marker]
        retained = [
            item for item in remaining
            if (item.key, item.version_id) in self.authorized_retained_versions
        ]
        if not remaining:
            result = CheckResult.PASS
        elif len(remaining) == len(retained) and not self.authority.may_read(scope):
            result = CheckResult.RESTRICTED
        else:
            result = CheckResult.FAIL
        return AdapterEvidence(
            adapter=self.name,
            scope=scope,
            authority_epoch=authority_epoch,
            result=result,
            trace_id=f"s3-{scope.tenant_id}-{scope.subject_id}-{authority_epoch}",
            inspected_count=len(remaining),
            forbidden_count=len(remaining) - len(retained),
            retention_allowed=result == CheckResult.RESTRICTED,
            uncertainty=None if result != CheckResult.FAIL else "Object versions remain",
        )

    def public_get(self, scope: SubjectScope, key: str) -> bytes | None:
        if not key.startswith(self.prefix(scope)) or not self.authority.may_read(scope):
            return None
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        content = response["Body"].read()
        return content if self.authority.may_read(scope) else None

    def read_version_for_ingestion(self, scope: SubjectScope, key: str, version_id: str) -> bytes | None:
        if not key.startswith(self.prefix(scope)) or not self.authority.may_read(scope):
            return None
        response = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version_id)
        content = response['Body'].read()
        return content if self.authority.may_read(scope) else None
