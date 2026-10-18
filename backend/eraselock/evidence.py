"""Scoped, integrity-checkable local evidence. KMS signing is a later AWS gate."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256

from contracts.adapter_protocol import AdapterEvidence


@dataclass(frozen=True, slots=True)
class EvidenceCertificate:
    certificate_version: str
    subject_key: str
    authority_epoch: int
    decision: str
    checks: tuple[dict[str, object], ...]
    errors: tuple[str, ...]
    generated_at: str
    integrity: str
    signature_status: str = "UNSIGNED_LOCAL"

    def payload(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("integrity")
        return result

    def verify_integrity(self) -> bool:
        return self.integrity == digest_payload(self.payload())


def digest_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


def certificate_for(
    subject_key: str,
    authority_epoch: int,
    decision: str,
    evidence: list[AdapterEvidence],
    errors: list[str] | None = None,
) -> EvidenceCertificate:
    checks = tuple(
        {
            "adapter": item.adapter,
            "scope": {
                "tenant_id": item.scope.tenant_id,
                "subject_id": item.scope.subject_id,
                "subject_generation": item.scope.subject_generation,
            },
            "authority_epoch": item.authority_epoch,
            "result": item.result.value,
            "trace_id": item.trace_id,
            "inspected_count": item.inspected_count,
            "forbidden_count": item.forbidden_count,
            "required": item.required,
            "retention_allowed": item.retention_allowed,
            "uncertainty": item.uncertainty,
        }
        for item in evidence
    )
    payload: dict[str, object] = {
        "certificate_version": "1.0",
        "subject_key": subject_key,
        "authority_epoch": authority_epoch,
        "decision": decision,
        "checks": checks,
        "errors": tuple(errors or []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signature_status": "UNSIGNED_LOCAL",
    }
    return EvidenceCertificate(**payload, integrity=digest_payload(payload))
