import json

import pytest

from backend.eraselock.domain import AdmissionClosed
from backend.workers.aws_sqs import AwsSQSProfileWorker, InvalidEvent, parse_profile_event
from backend.workers.local_queue import MessageResult
from contracts.adapter_protocol import SubjectScope


def body(**changes):
    payload = {
        "schema_version": "1.0", "event_id": "event-1",
        "tenant_id": "t01", "subject_id": "p017", "subject_generation": 4,
        "source_version": 2, "authority_epoch_seen": 0,
        "operation_type": "PROFILE_UPSERT", "occurred_at": "2026-10-19T00:00:00Z",
        "trace_id": "trace-1", "display_name": "Synthetic",
    }
    payload.update(changes)
    return json.dumps(payload)


class FakeSQS:
    def __init__(self, bodies):
        self.messages = [
            {"MessageId": f"msg-{i}", "ReceiptHandle": f"receipt-{i}", "Body": item}
            for i, item in enumerate(bodies)
        ]
        self.deleted = []
        self.dead_letters = []
        self.fail_dead_letter = False

    def receive_message(self, **kwargs):
        assert kwargs["QueueUrl"] == "source"
        return {"Messages": list(self.messages)}

    def send_message(self, **kwargs):
        assert kwargs["QueueUrl"] == "dlq"
        if self.fail_dead_letter:
            raise RuntimeError("SQS send failed")
        self.dead_letters.append(json.loads(kwargs["MessageBody"]))

    def delete_message(self, **kwargs):
        assert kwargs["QueueUrl"] == "source"
        self.deleted.append(kwargs["ReceiptHandle"])


class FakeOutcome:
    def __init__(self, outcome="APPLIED", replayed=False):
        self.outcome = outcome
        self.replayed = replayed


class FakeStore:
    def __init__(self, *, error=None, outcome=None):
        self.error = error
        self.outcome = outcome or FakeOutcome()
        self.calls = []

    def guarded_upsert_profile(self, scope, **kwargs):
        self.calls.append((scope, kwargs))
        if self.error:
            raise self.error
        return self.outcome


def test_sqs_profile_event_uses_guarded_write_and_acknowledges_success():
    sqs = FakeSQS([body()])
    store = FakeStore()
    result = AwsSQSProfileWorker(sqs, "source", "dlq", store).consume_once()
    assert result[0].result == MessageResult.APPLIED
    assert store.calls[0][0] == SubjectScope("t01", "p017", 4)
    assert store.calls[0][1]["operation_id"] == "event-1"
    assert sqs.deleted == ["receipt-0"]
    assert sqs.dead_letters == []


def test_erased_event_goes_to_dead_letter_before_ack():
    sqs = FakeSQS([body()])
    store = FakeStore(error=AdmissionClosed("closed"))
    result = AwsSQSProfileWorker(sqs, "source", "dlq", store).consume_once()
    assert result[0].result == MessageResult.REJECTED_ERASED
    assert sqs.dead_letters[0]["reason"] == "ERASED_GENERATION"
    assert sqs.deleted == ["receipt-0"]


def test_failed_dead_letter_or_store_does_not_acknowledge():
    sqs = FakeSQS([body()])
    sqs.fail_dead_letter = True
    with pytest.raises(RuntimeError):
        AwsSQSProfileWorker(sqs, "source", "dlq", FakeStore(error=AdmissionClosed())).consume_once()
    assert sqs.deleted == []
    sqs.fail_dead_letter = False
    with pytest.raises(RuntimeError):
        AwsSQSProfileWorker(sqs, "source", "dlq", FakeStore(error=RuntimeError("db down"))).consume_once()
    assert sqs.deleted == []


def test_invalid_event_is_dead_lettered_without_write():
    sqs = FakeSQS(["not json", body(subject_generation=True)])
    store = FakeStore()
    results = AwsSQSProfileWorker(sqs, "source", "dlq", store).consume_once()
    assert [item.result for item in results] == [MessageResult.DEAD_LETTER] * 2
    assert store.calls == []
    assert len(sqs.deleted) == len(sqs.dead_letters) == 2
    with pytest.raises(InvalidEvent):
        parse_profile_event(body(operation_type="ERASURE_COMMITTED"))


def test_replayed_event_is_reported_duplicate():
    sqs = FakeSQS([body()])
    result = AwsSQSProfileWorker(
        sqs, "source", "dlq", FakeStore(outcome=FakeOutcome(replayed=True))
    ).consume_once()
    assert result[0].result == MessageResult.DUPLICATE
