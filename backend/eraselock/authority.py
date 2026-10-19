"""Thread-safe, offline authority model used by local integration tests.

This model documents the operation semantics for a future DynamoDB adapter.
It is process-local and must not be used as a deployed multi-process authority.
"""

from __future__ import annotations

from dataclasses import replace
from threading import RLock
from time import monotonic, time
from typing import Callable
from uuid import uuid4

from contracts.adapter_protocol import AdapterEvidence, SubjectScope

from .domain import (
    AdmissionClosed,
    AdmissionPermit,
    Conflict,
    ControlState,
    ErasureDecision,
    EvidenceIncomplete,
    InflightUnresolved,
    NotFound,
    OperationState,
    PermitState,
    RecoveryApproval,
    RecoveryState,
    StaleEpoch,
    evidence_satisfies_release,
    validate_transition,
)


class InMemoryAuthority:
    def __init__(
        self,
        monotonic_clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], float] = time,
    ) -> None:
        self._lock = RLock()
        self._monotonic = monotonic_clock
        self._wall = wall_clock
        self._control: dict[SubjectScope, ControlState] = {}
        self._latest_generation: dict[tuple[str, str], int] = {}
        self._epochs: dict[str, int] = {}
        self._decisions: dict[str, ErasureDecision] = {}
        self._request_ids: dict[str, str] = {}
        self._permits: dict[str, AdmissionPermit] = {}
        self._permit_operations: dict[str, str] = {}
        self._recoveries: dict[str, RecoveryApproval] = {}

    def register_subject(self, scope: SubjectScope, *, authorized_new_generation: bool = False) -> None:
        if scope.subject_generation < 1 or not scope.tenant_id or not scope.subject_id:
            raise ValueError("A subject needs tenant, ID, and positive generation")
        key = (scope.tenant_id, scope.subject_id)
        with self._lock:
            if scope in self._control:
                raise Conflict("Subject generation already exists")
            latest = self._latest_generation.get(key)
            if latest is not None:
                prior = SubjectScope(*key, latest)
                if not authorized_new_generation or scope.subject_generation <= latest:
                    raise Conflict("A newer generation requires explicit authorization")
                if self._control[prior] not in (ControlState.ERASE_COMMITTED, ControlState.RESTRICTED):
                    raise Conflict("Previous generation must be closed first")
            self._control[scope] = ControlState.ACTIVE
            self._latest_generation[key] = scope.subject_generation

    def epoch(self, tenant_id: str) -> int:
        with self._lock:
            return self._epochs.get(tenant_id, 0)

    def state(self, scope: SubjectScope) -> ControlState:
        with self._lock:
            try:
                return self._control[scope]
            except KeyError as error:
                raise NotFound("Unknown subject generation") from error

    def may_read(self, scope: SubjectScope) -> bool:
        return self.state(scope) == ControlState.ACTIVE

    def erased_scopes(self, tenant_id: str) -> list[SubjectScope]:
        """Authoritative recovery coverage, never supplied by a request caller."""
        with self._lock:
            return [scope for scope, state in self._control.items()
                    if scope.tenant_id == tenant_id and state in
                    (ControlState.ERASE_COMMITTED, ControlState.RESTRICTED)]

    def acquire_permit(
        self, scope: SubjectScope, operation_id: str, *, lease_seconds: float = 30.0
    ) -> AdmissionPermit:
        if not operation_id or lease_seconds <= 0:
            raise ValueError("A permit needs an operation ID and positive lease")
        with self._lock:
            prior_id = self._permit_operations.get(operation_id)
            if prior_id is not None:
                prior = self._permits[prior_id]
                if prior.scope != scope:
                    raise Conflict("Operation ID reused for a different subject")
                return prior
            if self.state(scope) != ControlState.ACTIVE:
                raise AdmissionClosed("Subject generation is not active")
            permit = AdmissionPermit(
                permit_id=str(uuid4()),
                operation_id=operation_id,
                scope=scope,
                fence_epoch=self.epoch(scope.tenant_id),
                expires_at_monotonic=self._monotonic() + lease_seconds,
            )
            self._permits[permit.permit_id] = permit
            self._permit_operations[operation_id] = permit.permit_id
            return permit

    def close_admission(self, scope: SubjectScope, request_id: str) -> ErasureDecision:
        if not request_id:
            raise ValueError("An erasure request needs an idempotency ID")
        with self._lock:
            existing_id = self._request_ids.get(request_id)
            if existing_id is not None:
                decision = self._decisions[existing_id]
                if decision.scope != scope:
                    raise Conflict("Request ID reused for a different subject")
                return decision
            if self.state(scope) != ControlState.ACTIVE:
                raise Conflict("Subject generation is already closed")
            self._control[scope] = ControlState.ERASE_PENDING
            epoch = self._bump_epoch(scope.tenant_id)
            decision = ErasureDecision(
                decision_id=str(uuid4()),
                request_id=request_id,
                scope=scope,
                state=OperationState.ADMISSION_CLOSED,
                closed_epoch=epoch,
            )
            self._decisions[decision.decision_id] = decision
            self._request_ids[request_id] = decision.decision_id
            return decision

    def expire_permits(self) -> list[AdmissionPermit]:
        with self._lock:
            expired = []
            for permit_id, permit in self._permits.items():
                if permit.state == PermitState.OPEN and permit.expires_at_monotonic <= self._monotonic():
                    uncertain = replace(permit, state=PermitState.UNCERTAIN)
                    self._permits[permit_id] = uncertain
                    expired.append(uncertain)
            return expired

    def close_permit(
        self, permit_id: str, *, outcome: str, journal_confirmed: bool = False
    ) -> AdmissionPermit:
        if outcome not in ("COMMITTED", "FAILED"):
            raise ValueError("Outcome must be COMMITTED or FAILED")
        with self._lock:
            permit = self._get_permit(permit_id)
            if permit.state == PermitState.CLOSED:
                if permit.outcome != outcome:
                    raise Conflict("Permit already closed with another outcome")
                return permit
            if permit.state == PermitState.UNCERTAIN and not journal_confirmed:
                raise InflightUnresolved("Expired permit needs durable journal reconciliation")
            closed = replace(permit, state=PermitState.CLOSED, outcome=outcome)
            self._permits[permit_id] = closed
            return closed

    def unresolved_permits(self, scope: SubjectScope) -> list[AdmissionPermit]:
        with self._lock:
            return [
                permit for permit in self._permits.values()
                if permit.scope == scope and permit.state != PermitState.CLOSED
            ]

    def commit_erasure(self, decision_id: str) -> ErasureDecision:
        with self._lock:
            decision = self._get_decision(decision_id)
            if decision.state != OperationState.ADMISSION_CLOSED:
                if decision.committed_epoch is not None:
                    return decision
                raise Conflict("Erasure is not ready to commit")
            self.expire_permits()
            if self.unresolved_permits(decision.scope):
                raise InflightUnresolved("Write permits remain open or uncertain")
            validate_transition(decision.state, OperationState.COMMITTED)
            epoch = self._bump_epoch(decision.scope.tenant_id)
            committed = replace(decision, state=OperationState.COMMITTED, committed_epoch=epoch)
            self._decisions[decision_id] = committed
            self._control[decision.scope] = ControlState.ERASE_COMMITTED
            return committed

    def advance_decision(self, decision_id: str, state: OperationState) -> ErasureDecision:
        with self._lock:
            decision = self._get_decision(decision_id)
            if state == OperationState.VERIFIED:
                raise EvidenceIncomplete("Use verify_decision with adapter evidence")
            validate_transition(decision.state, state)
            updated = replace(decision, state=state)
            self._decisions[decision_id] = updated
            return updated

    def verify_decision(
        self, decision_id: str, evidence: list[AdapterEvidence], required_adapters: set[str]
    ) -> ErasureDecision:
        with self._lock:
            decision = self._get_decision(decision_id)
            if decision.state != OperationState.VALIDATING or decision.committed_epoch is None:
                raise Conflict("Decision must be in validation")
            if any(item.scope != decision.scope for item in evidence):
                raise EvidenceIncomplete("Evidence scope does not match decision")
            required_checks = {(adapter, decision.scope) for adapter in required_adapters}
            if not evidence_satisfies_release(evidence, required_checks, decision.committed_epoch):
                raise EvidenceIncomplete("Required verification did not pass")
            verified = replace(decision, state=OperationState.VERIFIED)
            self._decisions[decision_id] = verified
            return verified

    def decision(self, decision_id: str) -> ErasureDecision:
        with self._lock:
            return self._get_decision(decision_id)

    def prepare_recovery(
        self,
        *,
        candidate_id: str,
        tenant_id: str,
        validated_epoch: int,
        scope_digest: str,
        evidence: list[AdapterEvidence],
        required_checks: set[tuple[str, SubjectScope]],
        ttl_seconds: float = 300.0,
    ) -> RecoveryApproval:
        if not candidate_id or not scope_digest or ttl_seconds <= 0:
            raise ValueError("Recovery needs candidate, scope digest, and positive TTL")
        with self._lock:
            if candidate_id in self._recoveries:
                raise Conflict("Candidate already has an approval attempt")
            if validated_epoch != self.epoch(tenant_id) or self._has_pending(tenant_id):
                raise StaleEpoch("Authority changed during candidate validation")
            if any(item.scope.tenant_id != tenant_id for item in evidence):
                raise EvidenceIncomplete("Evidence tenant does not match candidate")
            if not evidence_satisfies_release(evidence, required_checks, validated_epoch):
                raise EvidenceIncomplete("Required recovery verification did not pass")
            approval = RecoveryApproval(
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                validated_epoch=validated_epoch,
                scope_digest=scope_digest,
                state=RecoveryState.APPROVED,
                valid_until_epoch_seconds=self._wall() + ttl_seconds,
            )
            self._recoveries[candidate_id] = approval
            return approval

    def release_recovery(self, candidate_id: str, *, expected_scope_digest: str) -> RecoveryApproval:
        """Local release decision only. It never changes DNS or cloud routing."""
        with self._lock:
            try:
                approval = self._recoveries[candidate_id]
            except KeyError as error:
                raise NotFound("Unknown recovery candidate") from error
            if approval.state != RecoveryState.APPROVED:
                raise Conflict("Candidate lacks a current approval")
            if approval.scope_digest != expected_scope_digest:
                raise Conflict("Candidate scope digest mismatch")
            if approval.valid_until_epoch_seconds <= self._wall():
                self._recoveries[candidate_id] = replace(approval, state=RecoveryState.STALE)
                raise StaleEpoch("Recovery approval expired")
            if approval.validated_epoch != self.epoch(approval.tenant_id) or self._has_pending(approval.tenant_id):
                self._recoveries[candidate_id] = replace(approval, state=RecoveryState.STALE)
                raise StaleEpoch("Authority epoch moved after approval")
            released = replace(approval, state=RecoveryState.RELEASED)
            self._recoveries[candidate_id] = released
            return released

    def _bump_epoch(self, tenant_id: str) -> int:
        self._epochs[tenant_id] = self._epochs.get(tenant_id, 0) + 1
        return self._epochs[tenant_id]

    def _has_pending(self, tenant_id: str) -> bool:
        return any(
            scope.tenant_id == tenant_id and state == ControlState.ERASE_PENDING
            for scope, state in self._control.items()
        )

    def _get_permit(self, permit_id: str) -> AdmissionPermit:
        try:
            return self._permits[permit_id]
        except KeyError as error:
            raise NotFound("Unknown permit") from error

    def _get_decision(self, decision_id: str) -> ErasureDecision:
        try:
            return self._decisions[decision_id]
        except KeyError as error:
            raise NotFound("Unknown erasure decision") from error
