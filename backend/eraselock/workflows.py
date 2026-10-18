"""Local deletion and quarantined recovery orchestration.

The AWS Step Functions implementation must preserve these gates. This module
does not switch any production route and its authority is process-local.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import EvidenceIncomplete, OperationState, RecoveryApproval
from backend.eraselock.evidence import EvidenceCertificate, certificate_for
from contracts.adapter_protocol import AdapterEvidence, DataAdapter, SubjectScope


@dataclass(frozen=True, slots=True)
class ErasureOutcome:
    decision_id: str
    state: OperationState
    certificate: EvidenceCertificate


class ErasureWorkflow:
    def __init__(self, authority: InMemoryAuthority, adapters: list[DataAdapter]) -> None:
        self.authority = authority
        self.adapters = adapters
        names = [adapter.name for adapter in adapters]
        if not names or len(names) != len(set(names)):
            raise ValueError("Required adapters must have unique names")

    def run(self, scope: SubjectScope, request_id: str) -> ErasureOutcome:
        decision = self.authority.close_admission(scope, request_id)
        if decision.state == OperationState.VERIFIED:
            evidence = [
                adapter.verify(scope, decision.committed_epoch) for adapter in self.adapters
            ]
            satisfied = all(item.satisfies_required_check() for item in evidence)
            certificate = certificate_for(
                f"{scope.tenant_id}/{scope.subject_id}/g{scope.subject_generation}",
                decision.committed_epoch,
                "PASS" if satisfied else "HOLD",
                evidence,
            )
            return ErasureOutcome(
                decision.decision_id,
                decision.state if satisfied else OperationState.EXCEPTION,
                certificate,
            )
        committed = self.authority.commit_erasure(decision.decision_id)
        if committed.state in (OperationState.COMMITTED, OperationState.EXCEPTION):
            self.authority.advance_decision(decision.decision_id, OperationState.REMEDIATING)
        failures: list[str] = []
        for adapter in self.adapters:
            try:
                adapter.remediate(scope, decision.decision_id)
            except Exception as error:
                failures.append(f"{adapter.name}: {type(error).__name__}")
        evidence: list[AdapterEvidence] = []
        for adapter in self.adapters:
            try:
                evidence.append(adapter.verify(scope, committed.committed_epoch))
            except Exception as error:
                failures.append(f"{adapter.name} verification: {type(error).__name__}")
        self.authority.advance_decision(decision.decision_id, OperationState.VALIDATING)
        if not failures:
            try:
                verified = self.authority.verify_decision(
                    decision.decision_id, evidence, {adapter.name for adapter in self.adapters}
                )
                state = verified.state
            except EvidenceIncomplete:
                state = self.authority.advance_decision(
                    decision.decision_id, OperationState.EXCEPTION
                ).state
        else:
            state = self.authority.advance_decision(
                decision.decision_id, OperationState.EXCEPTION
            ).state
        certificate = certificate_for(
            f"{scope.tenant_id}/{scope.subject_id}/g{scope.subject_generation}",
            committed.committed_epoch,
            "PASS" if state == OperationState.VERIFIED else "HOLD",
            evidence,
            failures,
        )
        return ErasureOutcome(decision.decision_id, state, certificate)


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    candidate_id: str
    approved: bool
    validated_epoch: int
    scope_digest: str
    certificate: EvidenceCertificate
    approval: RecoveryApproval | None = None


class RecoveryWorkflow:
    def __init__(self, authority: InMemoryAuthority, adapters: list[DataAdapter]) -> None:
        self.authority = authority
        self.adapters = adapters
        names = [adapter.name for adapter in adapters]
        if not names or len(names) != len(set(names)):
            raise ValueError("Required adapters must have unique names")

    def validate_candidate(
        self,
        candidate_id: str,
        tenant_id: str,
        erased_scopes: list[SubjectScope],
    ) -> RecoveryOutcome:
        if not erased_scopes or any(scope.tenant_id != tenant_id for scope in erased_scopes):
            raise ValueError("Candidate needs known erased scopes for its tenant")
        epoch = self.authority.epoch(tenant_id)
        scope_digest = "sha256:" + sha256(
            (candidate_id + "|" + "|".join(sorted(
                f"{s.tenant_id}:{s.subject_id}:{s.subject_generation}" for s in erased_scopes
            ))).encode()
        ).hexdigest()
        failures = []
        evidence: list[AdapterEvidence] = []
        for scope in erased_scopes:
            for adapter in self.adapters:
                try:
                    adapter.remediate(scope, candidate_id)
                    evidence.append(adapter.verify(scope, epoch))
                except Exception as error:
                    failures.append(f"{adapter.name}: {type(error).__name__}")
        approval = None
        if not failures:
            try:
                approval = self.authority.prepare_recovery(
                    candidate_id=candidate_id,
                    tenant_id=tenant_id,
                    validated_epoch=epoch,
                    scope_digest=scope_digest,
                    evidence=evidence,
                    required_checks={
                        (adapter.name, scope)
                        for adapter in self.adapters for scope in erased_scopes
                    },
                )
            except Exception as error:
                failures.append(f"release gate: {type(error).__name__}")
                approval = None
        certificate = certificate_for(
            f"candidate/{candidate_id}", epoch,
            "APPROVED" if approval is not None else "HOLD", evidence, failures,
        )
        return RecoveryOutcome(
            candidate_id, approval is not None, epoch, scope_digest, certificate, approval
        )

    def release(self, outcome: RecoveryOutcome) -> RecoveryApproval:
        if not outcome.approved:
            raise EvidenceIncomplete("Candidate was not approved")
        return self.authority.release_recovery(
            outcome.candidate_id, expected_scope_digest=outcome.scope_digest
        )
