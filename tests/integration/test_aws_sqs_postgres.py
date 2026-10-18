import json

from backend.adapters.postgres import PostgresAdapter
from backend.eraselock.authority import InMemoryAuthority
from backend.workers.aws_sqs import AwsSQSProfileWorker
from backend.workers.local_queue import MessageResult
from contracts.adapter_protocol import SubjectScope


class FakeSQS:
    def __init__(self, bodies):
        self.messages = [
            {"MessageId": f"m{i}", "ReceiptHandle": f"r{i}", "Body": body}
            for i, body in enumerate(bodies)
        ]
        self.dead_letters = []
        self.deleted = []

    def receive_message(self, **_kwargs):
        return {"Messages": self.messages}

    def send_message(self, **kwargs):
        self.dead_letters.append(json.loads(kwargs["MessageBody"]))

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs["ReceiptHandle"])


def _body(event_id, version):
    return json.dumps({
        "schema_version": "1.0", "event_id": event_id,
        "tenant_id": "t01", "subject_id": "p017", "subject_generation": 4,
        "source_version": version, "authority_epoch_seen": 0,
        "operation_type": "PROFILE_UPSERT", "occurred_at": "2026-10-19T00:00:00Z",
        "trace_id": "trace-1", "display_name": "Synthetic",
    })


def test_sqs_worker_real_postgres_duplicate_and_erased_replay(database):
    _base, _schema, dsn = database
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    store = PostgresAdapter(dsn, authority)
    source = FakeSQS([_body("event-1", 1), _body("event-1", 1)])
    results = AwsSQSProfileWorker(source, "source", "dlq", store).consume_once()
    assert [item.result for item in results] == [MessageResult.APPLIED, MessageResult.DUPLICATE]
    assert store.public_profile(scope)["source_version"] == 1

    decision = authority.close_admission(scope, "erase-1")
    authority.commit_erasure(decision.decision_id)
    store.remediate(scope, decision.decision_id)
    stale = FakeSQS([_body("event-old", 2)])
    result = AwsSQSProfileWorker(stale, "source", "dlq", store).consume_once()
    assert result[0].result == MessageResult.REJECTED_ERASED
    assert stale.dead_letters[0]["reason"] == "ERASED_GENERATION"
    assert store.public_profile(scope) is None
