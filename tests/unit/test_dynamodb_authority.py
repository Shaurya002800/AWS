from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from backend.eraselock.domain import AdmissionClosed, InflightUnresolved, OperationState, StaleEpoch
from backend.eraselock.dynamodb_authority import DynamoDBAuthority
from contracts.adapter_protocol import AdapterEvidence, CheckResult, SubjectScope


class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamoDB:
    def __init__(self):
        self.lock = Lock()
        self.item = None
        self.forced_conflicts = 0

    def get_item(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        with self.lock:
            return {} if self.item is None else {"Item": dict(self.item)}

    def put_item(self, **kwargs):
        with self.lock:
            if self.forced_conflicts:
                self.forced_conflicts -= 1
                raise ConditionalFailure()
            item = kwargs["Item"]
            previous = self.item
            if previous is None:
                if kwargs["ConditionExpression"] != "attribute_not_exists(pk)":
                    raise ConditionalFailure()
            elif int(kwargs["ExpressionAttributeValues"][":expected"]["N"]) != int(previous["version"]["N"]):
                raise ConditionalFailure()
            self.item = item


def test_dynamodb_authority_persists_admission_and_commit():
    client = FakeDynamoDB()
    scope = SubjectScope("t01", "p017", 1)
    first = DynamoDBAuthority(client, "rgx-demo")
    first.register_subject(scope)
    permit = first.acquire_permit(scope, "write-1")
    decision = first.close_admission(scope, "erase-1")
    restarted = DynamoDBAuthority(client, "rgx-demo")
    with pytest.raises(AdmissionClosed):
        restarted.acquire_permit(scope, "late-write")
    with pytest.raises(InflightUnresolved):
        restarted.commit_erasure(decision.decision_id)
    restarted.close_permit(permit.permit_id, outcome="COMMITTED")
    assert restarted.commit_erasure(decision.decision_id).state == OperationState.COMMITTED
    assert not first.may_read(scope)


def test_dynamodb_authority_retries_conditional_conflict():
    client = FakeDynamoDB()
    authority = DynamoDBAuthority(client, "rgx-demo")
    client.forced_conflicts = 1
    authority.register_subject(SubjectScope("t01", "p017", 1))
    assert client.item["version"]["N"] == "1"


def test_dynamodb_authority_epoch_invalidates_recovery():
    client = FakeDynamoDB()
    authority = DynamoDBAuthority(client, "rgx-demo")
    old = SubjectScope("t01", "p017", 1)
    new = SubjectScope("t01", "p018", 1)
    authority.register_subject(old)
    authority.register_subject(new)
    evidence = AdapterEvidence(
        adapter="rds", scope=old, authority_epoch=0,
        result=CheckResult.PASS, trace_id="trace", inspected_count=1, forbidden_count=0,
    )
    authority.prepare_recovery(
        candidate_id="candidate-1", tenant_id="t01", validated_epoch=0,
        scope_digest="sha256:abc", evidence=[evidence],
        required_checks={("rds", old)},
    )
    DynamoDBAuthority(client, "rgx-demo").close_admission(new, "erase-new")
    with pytest.raises(StaleEpoch):
        authority.release_recovery("candidate-1", expected_scope_digest="sha256:abc")


def test_dynamodb_authority_serializes_competing_writes():
    client = FakeDynamoDB()
    scope = SubjectScope("t01", "p017", 1)
    DynamoDBAuthority(client, "rgx-demo").register_subject(scope)
    with ThreadPoolExecutor(max_workers=8) as pool:
        permits = list(pool.map(
            lambda i: DynamoDBAuthority(client, "rgx-demo").acquire_permit(scope, f"op-{i}"),
            range(8),
        ))
    assert len({item.permit_id for item in permits}) == 8
    assert int(client.item["version"]["N"]) == 9
