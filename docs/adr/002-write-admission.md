# ADR 002 Subject-generation write admission

Status: accepted for the local prototype; persistent implementation pending.

Every participating sensitive writer requests a permit for `(tenant_id, subject_id, subject_generation)` and a unique `operation_id`. Admission and `ERASE_PENDING` must serialize at the authority. An admitted writer may finish after admission closes, but deletion cannot commit while that permit is unresolved. A permit whose lease expires enters `UNCERTAIN`; its RDS operation journal or equivalent durable outcome must be checked before it is cleared.

The writer must apply `source_version` guards and write its effect plus operation journal in one datastore transaction where possible. It closes the permit with `COMMITTED` or `FAILED` only after the durable outcome is known. Direct SQL or S3 writes that bypass the guard are outside the prototype's protection claim and should be denied by deployment IAM/database permissions where practical.

The local authority does not verify a journal itself. Its `journal_confirmed` argument on an uncertain permit is a contract for the reconciler, not a substitute for an actual durable check. Adapter integration tests must prove crash-after-commit reconciliation before the cloud implementation can claim the safety property.
