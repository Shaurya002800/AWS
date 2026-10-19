"""Callable task boundaries for offline orchestration tests.

Inputs never choose adapters, database endpoints, authority, or signing keys.
The host injects trusted dependencies. These are not deployed Lambda handlers.
"""
from dataclasses import asdict
from functools import wraps
from time import perf_counter
from contracts.adapter_protocol import SubjectScope
from backend.eraselock.workflows import ErasureWorkflow, RecoveryWorkflow


def observed(task):
    @wraps(task)
    def run(self, event, context=None):
        start = perf_counter()
        state = "HOLD"
        try:
            result = task(self, event, context)
            state = result.get("state", "APPROVED" if result.get("approved") else "HOLD")
            return result
        finally:
            if self.audit is not None:
                self.audit.record(task.__name__, state, perf_counter() - start)
    return run


class WorkflowTasks:
    def __init__(self, authority, adapters, signer, audit=None):
        self.authority, self.adapters, self.signer = authority, adapters, signer
        self.audit = audit
        self.erasure = ErasureWorkflow(authority, adapters)

    @observed
    def erase(self, event, context=None):
        if set(event) != {"scope", "request_id"}:
            raise ValueError("Unexpected erasure input")
        scope = SubjectScope(**event["scope"])
        # Positive local journals can recover a crash after data commit. Missing
        # journals are never interpreted as proof that a writer cannot finish.
        for adapter in self.adapters:
            reconcile = getattr(adapter, "reconcile_committed", None)
            if reconcile is not None:
                reconcile(scope)
        outcome = self.erasure.run(scope, event["request_id"])
        return {"decision_id": outcome.decision_id, "state": outcome.state.value,
                "evidence": self.signer.sign(outcome.certificate)}

    @observed
    def validate(self, event, context=None):
        if set(event) != {"candidate_id", "tenant_id"}:
            raise ValueError("Unexpected recovery input")
        # This task object must be constructed with candidate-scoped adapters.
        workflow = RecoveryWorkflow(self.authority, self.adapters)
        outcome = workflow.validate_candidate(event["candidate_id"], event["tenant_id"],
                                              self.authority.erased_scopes(event["tenant_id"]))
        return {"candidate_id": outcome.candidate_id, "approved": outcome.approved,
                "scope_digest": outcome.scope_digest,
                "evidence": self.signer.sign(outcome.certificate)}

    @observed
    def release(self, event, context=None):
        if set(event) != {"candidate_id", "scope_digest"}:
            raise ValueError("Unexpected release input")
        # Authority reloads persisted approval; never trust an 'approved' input.
        return asdict(self.authority.release_recovery(
            event["candidate_id"], expected_scope_digest=event["scope_digest"]))
