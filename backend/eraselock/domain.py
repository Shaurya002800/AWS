"""Pure EraseLock state rules. No AWS calls or storage side effects."""

from dataclasses import dataclass
from enum import StrEnum

from contracts.adapter_protocol import AdapterEvidence, SubjectScope


class ControlState(StrEnum):
    ACTIVE = "ACTIVE"
    ERASE_PENDING = "ERASE_PENDING"
    ERASE_COMMITTED = "ERASE_COMMITTED"
    RESTRICTED = "RESTRICTED"


class OperationState(StrEnum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    ADMISSION_CLOSED = "ADMISSION_CLOSED"
    COMMITTED = "COMMITTED"
    REMEDIATING = "REMEDIATING"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    EXCEPTION = "EXCEPTION"


class PermitState(StrEnum):
    OPEN = "OPEN"
    UNCERTAIN = "UNCERTAIN"
    CLOSED = "CLOSED"


class RecoveryState(StrEnum):
    ISOLATED = "ISOLATED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class EraseLockError(Exception):
    code = "ERASELOCK_ERROR"


class Conflict(EraseLockError):
    code = "CONFLICT"


class AdmissionClosed(EraseLockError):
    code = "ADMISSION_CLOSED"


class InflightUnresolved(EraseLockError):
    code = "INFLIGHT_UNRESOLVED"


class EvidenceIncomplete(EraseLockError):
    code = "EVIDENCE_INCOMPLETE"


class StaleEpoch(EraseLockError):
    code = "FENCE_STALE"


class NotFound(EraseLockError):
    code = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class ErasureDecision:
    decision_id: str
    request_id: str
    scope: SubjectScope
    state: OperationState
    closed_epoch: int
    committed_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class AdmissionPermit:
    permit_id: str
    operation_id: str
    scope: SubjectScope
    fence_epoch: int
    expires_at_monotonic: float
    state: PermitState = PermitState.OPEN
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryApproval:
    candidate_id: str
    tenant_id: str
    validated_epoch: int
    scope_digest: str
    state: RecoveryState
    valid_until_epoch_seconds: float


_TRANSITIONS: dict[OperationState, set[OperationState]] = {
    OperationState.REQUESTED: {OperationState.AUTHORIZED},
    OperationState.AUTHORIZED: {OperationState.ADMISSION_CLOSED},
    OperationState.ADMISSION_CLOSED: {OperationState.COMMITTED},
    OperationState.COMMITTED: {OperationState.REMEDIATING},
    OperationState.REMEDIATING: {OperationState.VALIDATING, OperationState.EXCEPTION},
    OperationState.VALIDATING: {OperationState.VERIFIED, OperationState.EXCEPTION},
    OperationState.EXCEPTION: {OperationState.REMEDIATING},
    OperationState.VERIFIED: set(),
}


def validate_transition(old: OperationState, new: OperationState) -> None:
    if new not in _TRANSITIONS[old]:
        raise Conflict(f"Invalid erasure transition: {old} -> {new}")


def evidence_satisfies_release(
    evidence: list[AdapterEvidence],
    required_checks: set[tuple[str, SubjectScope]],
    epoch: int,
) -> bool:
    if not required_checks or not evidence:
        return False
    by_check = {(item.adapter, item.scope): item for item in evidence}
    if len(by_check) != len(evidence):
        return False
    if any(item.required and (item.adapter, item.scope) not in required_checks for item in evidence):
        return False
    for check in required_checks:
        item = by_check.get(check)
        if item is None or item.authority_epoch != epoch or not item.required:
            return False
        if not item.satisfies_required_check():
            return False
    return True
