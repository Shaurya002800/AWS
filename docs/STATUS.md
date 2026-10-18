# RewindGuard X work status

Updated: 19 October 2026. Solo implementation supersedes the former team split; see `SOLO_IMPLEMENTATION.md`.

| Milestone | Owner | Status | Branch or PR | Evidence / next action |
| --- | --- | --- | --- | --- |
| EraseLock foundation and contracts | Owner | Implemented locally | `main` | SQLite restart-safe local authority; DynamoDB conditional-write adapter fake-client tested |
| Account eligibility and Region | Owner | Pending | — | Record account plan, active credits/free allowances, Region; keep billing details private |
| PostgreSQL journal and adapters | Owner | Local implementation passing tests | `main` | Migration, guarded writes, crash reconciliation, and isolated old-schema validation |
| Versioned objects, queue, index | Owner | Local implementation passing tests | `main` | In-memory S3 version model, delayed-event guard, PostgreSQL retrieval index, opt-in S3 adapter, and SQS worker tested with fake client plus real local database |
| Lab, oracle, and evidence | Owner | Local implementation passing tests | `main` | 49 tests pass; two-seed paired lab observed baseline 8/8 and protected 0/8 forbidden exposures; local dashboard visually checked |
| AWS infrastructure and deployment | Owner | Partial offline preparation | `main` | Core private S3/SQS/DynamoDB template and AWS adapters prepared; no deployment, account setup later at user's request |
| Final report and demo | Owner | Local report and runbook ready | `main` | `EVALUATION.md` and `AWS_READINESS.md` describe measurements and exact cloud gaps; live AWS evidence remains pending |

For each update, record commit, exact test command, observed result, unresolved blocker, and next action. Test the implementation independently before marking a gate complete.

Latest local verification: `RGX_TEST_POSTGRES_DSN='postgresql://shaurya@127.0.0.1:55432/postgres' .venv/bin/python -m pytest -q` passed 49 tests on 19 October 2026 (one dependency deprecation warning). `python -m lab.cli --output lab/reports/local-suite.json suite --seeds 42 43` completed eight pairs: baseline 8/8 forbidden exposures, protected 0/8. The local dashboard displayed baseline 1/1 and protected 0/1 for old database recovery. GitHub Actions succeeded for commit `650558b`; the SQS worker is the next checkpoint. AWS account eligibility and zero-spend deployment remain pending the owner's later setup.
