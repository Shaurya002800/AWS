# RewindGuard X solo implementation scope

Updated 19 October 2026. The owner superseded the earlier three-person work division and requested direct pushes to `main`. `TEAM_EXECUTION_PLAN.md` remains as historical planning context, but this document controls current work.

## Delivery target

Build a reproducible local prototype that demonstrates erasure, stale message replay, old database recovery, versioned object resurrection, and index re-ingestion against synthetic data. It must include an independent oracle, baseline-versus-protected runs, scoped evidence, tests, runbooks, and a minimal operator interface. Prepare AWS infrastructure and adapters, then deploy only services whose actual usage can meet the owner's zero out-of-pocket spending rule after account eligibility and Region are verified.

## Current constraints

- AWS account plan, credits, Region, and deadline are not yet recorded.
- The optional AWS SDK is installed in the local project environment; AWS CLI is not installed. No AWS credentials or cloud resources have been used.
- Docker Desktop's daemon is not running. PostgreSQL 15 binaries and psycopg are installed locally, so database integration can proceed without Docker.
- The lab uses `InMemoryAuthority` for isolated runs. The local API uses a durable SQLite authority. The DynamoDB authority has fake-client tests but no live AWS verification.
- No external services may be provisioned merely to show a course module. Actual deployed service claims require evidence.

## Work sequence

1. Consolidate existing plan and EraseLock foundation onto `main`; update current instructions and status.
2. Complete a PostgreSQL-backed local application with operation journal, version guards, lineage, records/documents/index tables, and backup/restore test support.
3. Add a versioned object simulator plus an optional real S3 adapter, local queue replay, guarded index build, and orchestrated erasure/recovery paths.
4. Add lab scenarios, independent oracle, repeatable baseline/treatment metrics, scoped evidence certificates, API, and a minimal dashboard or operator CLI.
5. Add infrastructure as code, IAM, cloud handlers, deployment/teardown scripts, and AWS integration tests that are opt-in and never run in normal CI.
6. Inspect account plan and active allowances, choose Region, estimate each resource, then run the cloud demonstration only if it meets zero out-of-pocket cost. Capture redacted evidence and clean up.
7. Finalize report, architecture and service-usage matrix, limitations, repeatable demo, and final test results.

## Acceptance gates

- Local tests pass from a fresh checkout without AWS credentials.
- Erasure closes admission; unresolved or uncertain writes prevent commitment until reconciled.
- A stale event cannot recreate a committed subject generation.
- A restored snapshot stays inaccessible until required checks pass and the approval epoch remains current.
- S3-like older versions and derived index entries are checked separately; retained or unknown locations are never called physically erased.
- The baseline visibly fails at least one seeded resurrection scenario while the protected path prevents exposure in the same scenario.
- Every claim in the report identifies tested paths, denominator, results, cost, and limitations.

Commit tested milestones directly to `main` and push. Never force push. Keep `docs/STATUS.md` up to date and preserve the zero-spend gate even when it limits cloud deployment.
