# ADR 001 Independent erasure authority

Status: accepted for the local prototype; AWS implementation pending.

The operational database may be restored from an older recovery point. Erasure decisions and write fences therefore require a separately protected authority that is not silently rolled back with that database. The local implementation uses `InMemoryAuthority` to test transitions and races without AWS costs. It is process-local and loses state on restart, so it is **never** an acceptable deployed authority.

The planned AWS implementation uses DynamoDB control and decision items with conditional writes and strongly consistent base-table reads where needed. The exact transaction design must preserve the behavior tested here: close admission, drain or reconcile permits, commit a decision, and invalidate stale release epochs. DynamoDB transactions do not make RDS or S3 writes atomic; adapters need idempotency journals and reconciliation.

Tenant-wide epochs are intentionally conservative. Any erasure in a tenant invalidates an earlier recovery approval for that tenant. Sharding by data scope is deferred until measured need justifies it.
