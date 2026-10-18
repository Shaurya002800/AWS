"""Durable, single-host authority for the local demo.

SQLite serializes every authority operation with BEGIN IMMEDIATE. Its file must
live outside the recoverable PostgreSQL database and must never be included in
the simulated old snapshot. This is a local stand-in for DynamoDB, not a
multi-host AWS authority.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from time import time
from typing import Any, Callable

from contracts.adapter_protocol import AdapterEvidence, SubjectScope

from .authority import InMemoryAuthority
from .domain import (
    AdmissionPermit, ControlState, ErasureDecision, OperationState,
    PermitState, RecoveryApproval, RecoveryState,
)


def _scope(data: dict[str, Any]) -> SubjectScope:
    return SubjectScope(**data)


class SQLiteAuthority:
    """A transactional snapshot of the complete local authority state.

    All callers must use this class against the same SQLite file. A read also
    reloads the latest committed state, so separate processes see each other.
    Permit deadlines use wall time because monotonic timestamps cannot survive
    a host restart. Clock rollback is a local-demo limitation.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS authority_state "
                    "(id INTEGER PRIMARY KEY CHECK (id=1), state_json TEXT NOT NULL)"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _load(self, conn: sqlite3.Connection) -> InMemoryAuthority:
        row = conn.execute("SELECT state_json FROM authority_state WHERE id=1").fetchone()
        return self.decode_snapshot(row[0] if row else None, clock=self.clock)

    @staticmethod
    def decode_snapshot(snapshot: str | None, *, clock: Callable[[], float] = time) -> InMemoryAuthority:
        authority = InMemoryAuthority(monotonic_clock=clock, wall_clock=clock)
        if snapshot is None:
            return authority
        data = json.loads(snapshot)
        authority._control = {
            _scope(item["scope"]): ControlState(item["state"])
            for item in data["control"]
        }
        authority._latest_generation = {
            (item["tenant_id"], item["subject_id"]): item["generation"]
            for item in data["latest_generation"]
        }
        authority._epochs = data["epochs"]
        authority._decisions = {
            item["decision_id"]: ErasureDecision(
                **{**item, "scope": _scope(item["scope"]),
                   "state": OperationState(item["state"])}
            ) for item in data["decisions"]
        }
        authority._request_ids = data["request_ids"]
        authority._permits = {
            item["permit_id"]: AdmissionPermit(
                **{**item, "scope": _scope(item["scope"]),
                   "state": PermitState(item["state"])}
            ) for item in data["permits"]
        }
        authority._permit_operations = data["permit_operations"]
        authority._recoveries = {
            item["candidate_id"]: RecoveryApproval(
                **{**item, "state": RecoveryState(item["state"])}
            ) for item in data["recoveries"]
        }
        return authority

    @staticmethod
    def _dump(authority: InMemoryAuthority) -> str:
        data = {
            "control": [
                {"scope": asdict(scope), "state": state.value}
                for scope, state in authority._control.items()
            ],
            "latest_generation": [
                {"tenant_id": tenant, "subject_id": subject, "generation": gen}
                for (tenant, subject), gen in authority._latest_generation.items()
            ],
            "epochs": authority._epochs,
            "decisions": [asdict(item) for item in authority._decisions.values()],
            "request_ids": authority._request_ids,
            "permits": [asdict(item) for item in authority._permits.values()],
            "permit_operations": authority._permit_operations,
            "recoveries": [asdict(item) for item in authority._recoveries.values()],
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def _call(self, method: str, *args: Any, mutate: bool = False, **kwargs: Any) -> Any:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                authority = self._load(conn)
                result = getattr(authority, method)(*args, **kwargs)
                if mutate:
                    conn.execute(
                        "INSERT INTO authority_state(id,state_json) VALUES(1,?) "
                        "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json",
                        (self._dump(authority),),
                    )
                return result

    def register_subject(self, scope: SubjectScope, *, authorized_new_generation: bool = False) -> None:
        self._call("register_subject", scope, authorized_new_generation=authorized_new_generation, mutate=True)

    def epoch(self, tenant_id: str) -> int:
        return self._call("epoch", tenant_id)

    def state(self, scope: SubjectScope) -> ControlState:
        return self._call("state", scope)

    def may_read(self, scope: SubjectScope) -> bool:
        return self._call("may_read", scope)

    def acquire_permit(self, scope: SubjectScope, operation_id: str, *, lease_seconds: float = 30.0) -> AdmissionPermit:
        return self._call("acquire_permit", scope, operation_id, lease_seconds=lease_seconds, mutate=True)

    def close_admission(self, scope: SubjectScope, request_id: str) -> ErasureDecision:
        return self._call("close_admission", scope, request_id, mutate=True)

    def expire_permits(self) -> list[AdmissionPermit]:
        return self._call("expire_permits", mutate=True)

    def close_permit(self, permit_id: str, *, outcome: str, journal_confirmed: bool = False) -> AdmissionPermit:
        return self._call("close_permit", permit_id, outcome=outcome, journal_confirmed=journal_confirmed, mutate=True)

    def unresolved_permits(self, scope: SubjectScope) -> list[AdmissionPermit]:
        return self._call("unresolved_permits", scope)

    def commit_erasure(self, decision_id: str) -> ErasureDecision:
        return self._call("commit_erasure", decision_id, mutate=True)

    def advance_decision(self, decision_id: str, state: OperationState) -> ErasureDecision:
        return self._call("advance_decision", decision_id, state, mutate=True)

    def verify_decision(self, decision_id: str, evidence: list[AdapterEvidence], required_adapters: set[str]) -> ErasureDecision:
        return self._call("verify_decision", decision_id, evidence, required_adapters, mutate=True)

    def decision(self, decision_id: str) -> ErasureDecision:
        return self._call("decision", decision_id)

    def prepare_recovery(self, **kwargs: Any) -> RecoveryApproval:
        return self._call("prepare_recovery", mutate=True, **kwargs)

    def release_recovery(self, candidate_id: str, *, expected_scope_digest: str) -> RecoveryApproval:
        return self._call("release_recovery", candidate_id, expected_scope_digest=expected_scope_digest, mutate=True)
