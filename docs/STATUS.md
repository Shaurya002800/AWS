# RewindGuard X work status

## Review-ready checkpoint — 20 October 2026

All nine current-serving live checks passed at 13:36 UTC, including a real SQL
profile read, erasure, KMS verification/tamper rejection, object-version removal
and tenant isolation. No new snapshot or RDS instance was created by this check.
The earlier full recovery evaluation passed all 15 checks; these are separate scopes.
A private owner login has its first-login password-change challenge verified;
credentials are ignored locally and the owner must choose their own password.
The approved database is resumed only for a short review window; source stays stopped.
Startup now defaults to 30 minutes with cleanup polling every 30 minutes.
Sign-out and a direct completed-evaluation link finish the UI review flow.
Checkpoint verification is now read-only: it cannot redeploy a stale code patch
or stop an intentional review session. See REVIEW_GUIDE.md.

Validation: 88 Python tests and seven frontend tests pass locally; 16 PostgreSQL
tests require a DSN and run against the GitHub CI PostgreSQL service.

Earlier entries below are historical checkpoints.

## Notebook-style demo UI — 20 October 2026

The cloud demo was redesigned around the supplied notebook/scrapbook reference:
ruled paper, bold title, taped illustrations, colored stickers, dark experiment
cards and yellow evidence panels. Secure sign-in, operator restrictions and
PKCE remain. Structured evidence renders actual returned counts, held/released
states and signing status; run JSON can be downloaded. No placeholder success
metrics are shown. Details and validation limits: UI_REDESIGN.md.

Validation: 79 Python tests passed, 16 local PostgreSQL tests skipped; six
frontend behavior tests passed. OpenAPI has no diff. Mobile/desktop DOM checks
showed no horizontal overflow; browser screenshot capture was unavailable.
The UI deployment reached UPDATE_COMPLETE; hosted HTML/CSS/JS checks passed.
Bootstrap remains disabled and both databases are confirmed stopped.
The earlier 15-check real-cloud recovery evaluation remains a separate result.

## Current verified cloud and CI checkpoint — 20 October 2026

The full real-cloud evaluation passed all 15 checks. The restored snapshot had
one target profile, document and index chunk; reconciliation reduced each count
to zero. Recovery reached RELEASED. Real KMS verification and tamper rejection,
JWT rejection, tenant isolation, private encrypted RDS and IAM authentication passed.
The measured workflow duration was 472.34 seconds. See FINAL_CLOUD_EVALUATION.md.

Final operational verification at 12:51 UTC confirmed UPDATE_COMPLETE, bootstrap
disabled, both databases stopped, zero synthetic test snapshots, and an active
Free plan with positive credits. There is no fixed USD 25 reserve. Credit metering
can lag; the observed USD 0.0 change during evaluation is not a finalized bill.

GitHub's earlier red check came from the CI environment missing boto3/botocore:
it installed only the dev extra, while the new mocked cloud tests need the aws
extra. The workflow now installs both; GitHub Actions runtime deprecation warnings
are addressed with current official action releases. The documented evaluator
now always runs database/snapshot cleanup in finally, covered on success/failure.
Local verification: 78 passed, 16 skipped for absent local PostgreSQL DSN, one
dependency deprecation warning. OpenAPI regeneration produced no diff. GitHub CI
runs the PostgreSQL integration tests. Its new result is tracked in the README badge.

Earlier entries below are historical checkpoints.

## Live cloud application deployed; evaluation running (20 October 2026)

The owner explicitly authorized direct Python deployment. Stack rewindguard-x-app
reached CREATE_COMPLETE in ap-southeast-2. Private encrypted PostgreSQL 17.11
schema initialization and IAM database-user setup succeeded. Hosted health,
page, JavaScript and stylesheet returned HTTP 200. Bootstrap endpoint deletion
completed; bootstrap Lambda deletion is still finishing in CloudFormation.

An initial synthetic seed failed because S3 HeadObject returns 403 for a missing
key without ListBucket. Conditional immutable creation fixes that without adding
permissions. Twelve cloud-specific tests and the full suite (76 passed, 16 local
PostgreSQL skips) pass. The interrupted seed was replayed with its original
operation ID against AWS and succeeded. The full snapshot/erase/restore evaluation
is running; no successful full-recovery result or final bill is claimed yet.
Immediate database stop and disposable snapshot cleanup are attached to evaluation.
See CLOUD_DEMO_RUNBOOK.md for operator setup and demo start/stop commands.

Earlier checkpoint notes below are historical.

## Lowest-credit configuration prepared (20 October 2026)

The owner authorized removing the USD 25 minimum and minimizing credits.
Deployment now requires FREE/ACTIVE with positive credits only; no fixed reserve.
Synthetic RDS automated backups are disabled, explicit restore snapshots remain,
logs retain one day, and application teardown retains no synthetic final snapshot.
The plan is one evaluation followed by immediate database stop. There is no Paid
upgrade. Remaining storage/key charges still consume credits while retained.

Verification: full pytest 74 passed, 16 PostgreSQL tests skipped (no local DSN),
one dependency deprecation warning; local cfn-lint passes. Free-plan API check
observed FREE/ACTIVE with positive credits. No full application deployment was
confirmed. Automatic review blocked the deployment due to PowerShell Microsoft
telemetry, then rejected direct Python execution without that shell because it
requires explicit authorization for that execution method. No further deployment
attempts will run until that specific authorization is supplied.

## Expanded application prepared, awaiting upload authorization (20 October 2026)

Private RDS and isolated restore, Lambda/Step Functions, Cognito/JWT identity,
scoped IAM, KMS evidence, monitoring, cleanup, and real-cloud evaluation scripts
are implemented locally. See FULL_CLOUD_DEPLOYMENT.md. Local cfn-lint passes.
The expanded stack has NOT been uploaded or deployed: automatic approval review
blocked sending the new application template to AWS pending explicit approval.
The existing verified core remains live. Full cloud evaluation and measured
costs remain pending; no production completion is claimed.

## Live AWS core verified (20 October 2026)

Stack `rewindguard-x-core` deployed in `ap-southeast-2`: AuthorityTable,
DocumentBucket, DeadLetterQueue, and ProfileEventQueue all reached
CREATE_COMPLETE. The stack passed the active FREE-plan and credit-reserve gate.
The PowerShell tag parsing issue was fixed by passing `infra/core-tags.json`.

`scripts/connect_aws.ps1` completed successfully. Its real AWS smoke test
observed VERIFIED erasure, zero remaining subject S3 versions, rejection of a
new S3 write for the erased subject, rejection of the matching delayed SQS
message, and a valid local HMAC evidence envelope. Evidence is kept privately
at `evidence/private/aws-smoke.json`, not in Git.

This is HYBRID_AWS_CORE_LOCAL_RECORDS: DynamoDB, S3, SQS and the dead-letter
queue are live. SQLite operational records/index and API execution remain
local. RDS, Lambda, hosted API, and KMS signing are not deployed. Resources
remain available for the next project run. Credit consumption is not a
measured bill; Free-plan status and reserve are rechecked on each cloud startup.
The earlier credential-cache blocker below is resolved for this run.

## AWS core connection preparation (20 October 2026, Windows)

AWS CLI login and toolkit setup were verified using profile `rewindguard`.
The project reported FREE/ACTIVE with credits verified privately, expiry 20 April 2027,
and selected Region `ap-southeast-2`. The original `infra/core.json` passed
CloudFormation validation. No stack creation has succeeded in this session:
subsequent deployment attempts fail on the local AWS login-cache file with
`Permission denied`, before an AWS request is sent.

The hybrid runtime now discovers core outputs from CloudFormation and wires
real DynamoDB/S3/SQS adapters to local records/index. `scripts/connect_aws.ps1`
performs the Free-plan/credit gate, checks the exact four-resource change set,
deploys, and runs `scripts/aws_smoke.py`. The smoke test is prepared, NOT yet
live-verified. See `AWS_CORE_CONNECTION.md` for scope, cost envelope, and teardown.

Local verification: `.venv/Scripts/python.exe -m pytest -q --basetemp
.local-authority/pytest-final-cloud` passed 64 tests; 16 PostgreSQL-dependent
tests were skipped because no local test DSN is configured. One dependency
deprecation warning remains. Compilation and PowerShell syntax checks pass.
Current blocker: refresh browser-based AWS login in the user's normal terminal.

## 20 October 2026 local checkpoint

Recovery task validation and release now require a host-supplied candidate ID
binding. A live runtime cannot approve or release a recovery candidate, and a
request cannot substitute another candidate ID. The offline restore demo uses
candidate-scoped adapters with that binding. The independent authority still
checks approval digest, epoch, and reuse at release. This task-boundary check
does not itself prove cloud network isolation or candidate identity; those
remain deployment gates.

Verification: `python3 -m unittest discover -s tests/offline -q` passed 21
checks; `python3 -m scripts.offline_demo` observed 3 old raw rows, 0 after
reconciliation, and a RELEASED candidate; local PostgreSQL integration with
`RGX_TEST_POSTGRES_DSN='postgresql://shaurya@127.0.0.1:55432/postgres'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q` passed 72 tests.
No AWS services were called. An advanced AWS account reached root/MFA setup,
but payment verification and active Free-plan/credit confirmation remain
incomplete. The first Builder ID project had an address-verification failure.
See `AWS_FIRST_TIME_SETUP.md`; do not deploy resources yet.

## Current synchronized checkpoint

See `OFFLINE_COMPLETION.md` for the current implementation and run commands.
The erase API now invokes the full configured workflow; signed local sessions,
HMAC evidence envelopes, local task monitoring, dependency-injected handlers,
and reference workflow definitions are added. Recovery scope omission and stale
release checks are covered. No AWS services have been used. The teammate's
commits `634893f`, `f335da5`, and `328b872` were fast-forwarded into local
`main` on 19 October 2026.

Current verification: `python -m unittest discover -s tests/offline -q` passes
19 acceptance checks. `python -m scripts.offline_demo` observes 3 historic raw
rows and 0 after reconciliation, VERIFIED erasure, RELEASED candidate, and a
valid local evidence envelope. The full FastAPI/PostgreSQL suite was rerun on
the owner's Mac: `RGX_TEST_POSTGRES_DSN='postgresql://shaurya@127.0.0.1:55432/postgres' .venv/bin/python -m pytest -q` passed 70 tests. The remote CI failed only its generated OpenAPI check after the teammate's changes; the contract was regenerated locally. Complete cloud IAM, packaging, RDS route
switching, production identity, and live evaluation remain pending.
The repository is ready for first-time AWS account security and billing setup,
but not for deploying the complete application. See `AWS_FIRST_TIME_SETUP.md`.

The status table and test counts below describe the earlier source checkpoint.

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

Latest local verification: `RGX_TEST_POSTGRES_DSN='postgresql://shaurya@127.0.0.1:55432/postgres' .venv/bin/python -m pytest -q` passed 49 tests on 19 October 2026 (one dependency deprecation warning). `python -m lab.cli --output lab/reports/local-suite.json suite --seeds 42 43` completed eight pairs: baseline 8/8 forbidden exposures, protected 0/8. The local dashboard displayed baseline 1/1 and protected 0/1 for old database recovery. GitHub Actions succeeded for commits `650558b` and `75e95e7`. The latest local API was restarted on loopback and returned healthy. AWS account eligibility and zero-spend deployment remain pending the owner's later setup.
