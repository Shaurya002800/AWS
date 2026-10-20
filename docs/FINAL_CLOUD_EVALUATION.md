# RewindGuard X — cloud evaluation

Measured at 2026-10-20T14:13:53.756460+00:00 in `ap-southeast-2`. Run: `54008557ae134b2c807e18d38fa79ce1`. This is a synthetic research
prototype result, not a production security certification or legal assessment.

## Actual deployment

Private RDS PostgreSQL, isolated snapshot restoration, Lambda workers,
Step Functions recovery, API Gateway JWT authorization, Cognito authorization
code/PKCE client, asymmetric KMS evidence signatures, scoped service IAM roles,
CloudWatch logs/alarms, and bounded cleanup. The earlier S3/DynamoDB/SQS core
remains in use. No public database or NAT gateway is created.

## Observed checks

| Check | Observed pass |
| --- | --- |
| anonymous_request_denied | True |
| forged_token_denied | True |
| authenticated_operator | True |
| cross_tenant_request_denied | True |
| rds_profile_and_index_seeded | True |
| old_snapshot_contains_seeded_data | True |
| guarded_candidate_read_denied_before_reconciliation | True |
| candidate_reconciled | True |
| recovery_published | True |
| published_api_does_not_expose_erased_profile | True |
| real_kms_signature_valid | True |
| evidence_tamper_rejected | True |
| published_database_is_private | True |
| database_storage_encrypted | True |
| iam_database_authentication_enabled | True |

## Recovery observation

The candidate's raw pre-reconciliation counts were `{'profiles': 1, 'documents': 1, 'index_chunks': 1}`;
post-reconciliation counts were `{'profiles': 0, 'documents': 0, 'index_chunks': 0}`. The guarded profile
before reconciliation was `None`. Publication result:
`{'state': 'RELEASED', 'validated_epoch': 10}`. The raw SQL oracle is confined to the trusted recovery
worker and is not a public endpoint. This compares historical raw state against
the guarded application; it is not a matched public-exposure-rate experiment.

Measured cloud recovery duration: 462.77 seconds.
Authenticated read median: 2486.42 ms; p95:
2911.22 ms across 10 samples.
Small-sample p95 is descriptive, not a throughput benchmark.

## Cost evidence and limits

The Free-plan API was checked before and after the run. Remaining credits are
kept privately with the raw evidence. Difference observed during the run:
0.0 USD. This is not a final bill: metering
and credits can be delayed. Database configuration, workflow duration, and sampled request latencies are
recorded in private evidence. No claim of an exact finalized charge is made.
The project must remain on the Free plan for the owner's zero-out-of-pocket
requirement. There is no fixed credit reserve. Workflow candidates carry a two-hour fallback expiry;
review startup sets the serving window to 30 minutes by default. The standard evaluator
stops databases after evaluation; an explicitly requested review session can instead
keep the approved serving database available briefly. Cleanup checks every 30 minutes,
with immediate cleanup requested after publication for superseded disposable candidates. Their
synthetic snapshots are removed. Stopped RDS storage, secrets, the signing key,
and monitoring can continue using credits. Recheck usage before each demo.

## Boundaries

Only mediated reads, writes, queue consumers, known S3 versions and configured
database/index tables are covered. Privileged administrators, unknown copies,
and training-weight unlearning are outside the guarantee. Ledger/journal,
lineage, and evidence metadata are retained deliberately. One successful cloud
restore does not establish universal safety, scale, novelty, or compliance.
The previous paired local PostgreSQL suite is a separate experiment.
