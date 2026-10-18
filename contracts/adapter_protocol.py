"""Stable adapter boundary shared by the two implementation owners.

Adapters are responsible for their own idempotent effects and independent
verification. The authority never interprets an adapter invocation as proof.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    RESTRICTED = "RESTRICTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"


@dataclass(frozen=True, slots=True)
class SubjectScope:
    tenant_id: str
    subject_id: str
    subject_generation: int


@dataclass(frozen=True, slots=True)
class Location:
    adapter: str
    locator: str
    source_version: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterEvidence:
    adapter: str
    scope: SubjectScope
    authority_epoch: int
    result: CheckResult
    trace_id: str
    inspected_count: int
    forbidden_count: int
    required: bool = True
    retention_allowed: bool = False
    uncertainty: str | None = None

    def satisfies_required_check(self) -> bool:
        if not self.required:
            return True
        if self.result in (CheckResult.PASS, CheckResult.NOT_APPLICABLE):
            return self.forbidden_count == 0 and self.uncertainty is None
        if self.result == CheckResult.RESTRICTED:
            return self.retention_allowed and self.uncertainty is None
        return False


class DataAdapter(Protocol):
    name: str

    def discover(self, scope: SubjectScope) -> list[Location]: ...

    def remediate(self, scope: SubjectScope, decision_id: str) -> None: ...

    def verify(self, scope: SubjectScope, authority_epoch: int) -> AdapterEvidence: ...
