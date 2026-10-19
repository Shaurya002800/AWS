"""Optimistically fenced DynamoDB authority for a small AWS course demo.

The complete authority snapshot is one DynamoDB item. Conditional writes make
admission, erasure and release decisions atomic across Lambda processes. This
single-item design is deliberately limited to a small synthetic corpus; it
must be redesigned and load-tested for a production workload.
"""

from __future__ import annotations

from time import sleep, time
from typing import Any, Callable

from .sqlite_authority import SQLiteAuthority


class DynamoDBAuthority:
    _KEY = {"pk": {"S": "AUTHORITY#v1"}}

    def erased_scopes(self, tenant_id: str):
        return self._call("erased_scopes", tenant_id)

    def __init__(
        self, client: Any, table_name: str, *, clock: Callable[[], float] = time,
        max_conflicts: int = 8,
    ) -> None:
        if not table_name or max_conflicts < 1:
            raise ValueError("Table name and positive retry limit are required")
        self.client = client
        self.table_name = table_name
        self.clock = clock
        self.max_conflicts = max_conflicts

    def _read(self) -> tuple[int, Any]:
        response = self.client.get_item(
            TableName=self.table_name, Key=self._KEY, ConsistentRead=True
        )
        item = response.get("Item")
        if item is None:
            return 0, SQLiteAuthority.decode_snapshot(None, clock=self.clock)
        version = int(item["version"]["N"])
        return version, SQLiteAuthority.decode_snapshot(item["state_json"]["S"], clock=self.clock)

    def _call(self, method: str, *args: Any, mutate: bool = False, **kwargs: Any) -> Any:
        attempts = self.max_conflicts if mutate else 1
        for attempt in range(attempts):
            version, authority = self._read()
            result = getattr(authority, method)(*args, **kwargs)
            if not mutate:
                return result
            snapshot = SQLiteAuthority._dump(authority)
            if len(snapshot.encode("utf-8")) > 350_000:
                raise RuntimeError("Authority snapshot exceeds safe demo item size")
            try:
                self.client.put_item(
                    TableName=self.table_name,
                    Item={
                        **self._KEY,
                        "version": {"N": str(version + 1)},
                        "state_json": {"S": snapshot},
                    },
                    ConditionExpression=(
                        "attribute_not_exists(pk)" if version == 0
                        else "#version = :expected"
                    ),
                    **({} if version == 0 else {
                        "ExpressionAttributeNames": {"#version": "version"},
                        "ExpressionAttributeValues": {":expected": {"N": str(version)}},
                    }),
                )
                return result
            except Exception as error:
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code != "ConditionalCheckFailedException":
                    raise
                if attempt + 1 == attempts:
                    raise RuntimeError("Authority contention exceeded retry limit") from error
                sleep(min(0.01 * 2**attempt, 0.2))
        raise AssertionError("Unreachable retry exit")

    def register_subject(self, scope, *, authorized_new_generation=False):
        return self._call("register_subject", scope, authorized_new_generation=authorized_new_generation, mutate=True)

    def epoch(self, tenant_id):
        return self._call("epoch", tenant_id)

    def state(self, scope):
        return self._call("state", scope)

    def may_read(self, scope):
        return self._call("may_read", scope)

    def acquire_permit(self, scope, operation_id, *, lease_seconds=30.0):
        return self._call("acquire_permit", scope, operation_id, lease_seconds=lease_seconds, mutate=True)

    def close_admission(self, scope, request_id):
        return self._call("close_admission", scope, request_id, mutate=True)

    def expire_permits(self):
        return self._call("expire_permits", mutate=True)

    def close_permit(self, permit_id, *, outcome, journal_confirmed=False):
        return self._call("close_permit", permit_id, outcome=outcome, journal_confirmed=journal_confirmed, mutate=True)

    def unresolved_permits(self, scope):
        return self._call("unresolved_permits", scope)

    def commit_erasure(self, decision_id):
        return self._call("commit_erasure", decision_id, mutate=True)

    def advance_decision(self, decision_id, state):
        return self._call("advance_decision", decision_id, state, mutate=True)

    def verify_decision(self, decision_id, evidence, required_adapters):
        return self._call("verify_decision", decision_id, evidence, required_adapters, mutate=True)

    def decision(self, decision_id):
        return self._call("decision", decision_id)

    def prepare_recovery(self, **kwargs):
        return self._call("prepare_recovery", mutate=True, **kwargs)

    def release_recovery(self, candidate_id, *, expected_scope_digest):
        return self._call("release_recovery", candidate_id, expected_scope_digest=expected_scope_digest, mutate=True)
