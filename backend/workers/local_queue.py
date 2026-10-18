"""Deterministic local queue for stale-event and duplicate-delivery experiments."""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from backend.adapters.postgres import PostgresAdapter
from backend.eraselock.domain import AdmissionClosed, Conflict
from contracts.adapter_protocol import SubjectScope


class MessageResult(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE_VERSION = "STALE_VERSION"
    REJECTED_ERASED = "REJECTED_ERASED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True, slots=True)
class ProfileEvent:
    event_id: str
    scope: SubjectScope
    source_version: int
    display_name: str


class LocalProfileQueue:
    def __init__(self, store: PostgresAdapter) -> None:
        self.store = store
        self.pending: deque[ProfileEvent] = deque()
        self.dead_letter: list[tuple[ProfileEvent, str]] = []

    def send(self, event: ProfileEvent) -> None:
        self.pending.append(event)

    def consume_one(self) -> MessageResult:
        event = self.pending.popleft()
        try:
            outcome = self.store.guarded_upsert_profile(
                event.scope,
                operation_id=event.event_id,
                source_version=event.source_version,
                display_name=event.display_name,
            )
        except AdmissionClosed:
            self.dead_letter.append((event, "ERASED_GENERATION"))
            return MessageResult.REJECTED_ERASED
        except Conflict as error:
            self.dead_letter.append((event, str(error)))
            return MessageResult.DEAD_LETTER
        if outcome.replayed:
            return MessageResult.DUPLICATE
        if outcome.outcome == "STALE_VERSION":
            return MessageResult.STALE_VERSION
        return MessageResult.APPLIED
