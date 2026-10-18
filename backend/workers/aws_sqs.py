"""SQS profile consumer with guarded writes and explicit poison-message handling.

Construct with an injected boto3 SQS client and a PostgresAdapter. No network
request occurs at import. Standard SQS is at-least-once, so the PostgreSQL
operation journal, rather than queue delivery, supplies idempotency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.adapters.postgres import PostgresAdapter
from backend.eraselock.domain import AdmissionClosed, Conflict
from backend.workers.local_queue import MessageResult, ProfileEvent
from contracts.adapter_protocol import SubjectScope


class InvalidEvent(ValueError):
    pass


def parse_profile_event(body: str) -> ProfileEvent:
    try:
        data = json.loads(body)
    except (TypeError, json.JSONDecodeError) as error:
        raise InvalidEvent("Message body is not JSON") from error
    if not isinstance(data, dict):
        raise InvalidEvent("Message body must be an object")
    required_text = ("event_id", "tenant_id", "subject_id", "display_name")
    if any(not isinstance(data.get(key), str) or not data[key] for key in required_text):
        raise InvalidEvent("Profile event is missing a required string")
    if data.get("schema_version") != "1.0" or data.get("operation_type") != "PROFILE_UPSERT":
        raise InvalidEvent("Unsupported event schema or operation")
    for key in ("subject_generation", "source_version", "authority_epoch_seen"):
        if type(data.get(key)) is not int or data[key] < (0 if key == "authority_epoch_seen" else 1):
            raise InvalidEvent(f"Invalid {key}")
    if not isinstance(data.get("occurred_at"), str) or not data["occurred_at"]:
        raise InvalidEvent("Missing occurred_at")
    if not isinstance(data.get("trace_id"), str) or not data["trace_id"]:
        raise InvalidEvent("Missing trace_id")
    return ProfileEvent(
        event_id=data["event_id"],
        scope=SubjectScope(data["tenant_id"], data["subject_id"], data["subject_generation"]),
        source_version=data["source_version"],
        display_name=data["display_name"],
    )


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    message_id: str
    result: MessageResult


class AwsSQSProfileWorker:
    def __init__(
        self, client: Any, queue_url: str, dead_letter_queue_url: str,
        store: PostgresAdapter,
    ) -> None:
        if not queue_url or not dead_letter_queue_url or queue_url == dead_letter_queue_url:
            raise ValueError("Distinct source and dead-letter queues are required")
        self.client = client
        self.queue_url = queue_url
        self.dead_letter_queue_url = dead_letter_queue_url
        self.store = store

    def consume_once(self, *, wait_seconds: int = 0) -> list[ConsumeResult]:
        if not 0 <= wait_seconds <= 20:
            raise ValueError("SQS wait_seconds must be between 0 and 20")
        response = self.client.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=10,
            WaitTimeSeconds=wait_seconds, VisibilityTimeout=60,
        )
        results = []
        for message in response.get("Messages", []):
            results.append(self._consume(message))
        return results

    def _consume(self, message: dict[str, str]) -> ConsumeResult:
        # Missing SQS metadata is an infrastructure failure; do not ack.
        message_id = message["MessageId"]
        receipt = message["ReceiptHandle"]
        body = message["Body"]
        try:
            event = parse_profile_event(body)
        except InvalidEvent as error:
            self._dead_letter(message_id, body, "INVALID_EVENT", str(error))
            result = MessageResult.DEAD_LETTER
        else:
            try:
                outcome = self.store.guarded_upsert_profile(
                    event.scope, operation_id=event.event_id,
                    source_version=event.source_version,
                    display_name=event.display_name,
                )
            except AdmissionClosed:
                self._dead_letter(message_id, body, "ERASED_GENERATION", "Admission closed")
                result = MessageResult.REJECTED_ERASED
            except Conflict as error:
                self._dead_letter(message_id, body, "CONFLICT", str(error))
                result = MessageResult.DEAD_LETTER
            else:
                result = (
                    MessageResult.DUPLICATE if outcome.replayed else
                    MessageResult.STALE_VERSION if outcome.outcome == "STALE_VERSION" else
                    MessageResult.APPLIED
                )
        # If database commit, dead-letter send, or SQS ack fails, SQS will
        # redeliver. The operation journal makes a repeated write harmless.
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)
        return ConsumeResult(message_id, result)

    def _dead_letter(self, message_id: str, body: str, reason: str, detail: str) -> None:
        self.client.send_message(
            QueueUrl=self.dead_letter_queue_url,
            MessageBody=json.dumps({
                "source_message_id": message_id,
                "reason": reason,
                "detail": detail,
                "original_body": body,
            }, sort_keys=True),
        )
