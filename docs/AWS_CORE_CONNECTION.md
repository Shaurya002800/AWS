# AWS core connection

Verified live on 20 October 2026: the four-resource stack deployed successfully
and the synthetic smoke test passed erasure, S3 version removal, stale-write
blocking, and matching SQS replay rejection. Records and index remain local.

The `rewindguard` profile uses browser login, not committed access keys. All
project resources are restricted to `ap-southeast-2`. An active FREE plan and at
least USD 10 remaining credits are required by both the connection script and
runtime. Neither upgrades the project to Paid. This is a credit reserve check,
not a spending cap on a Paid plan.

## Scope and cost envelope

The core stack has four AWS resources: one on-demand DynamoDB table, a private
versioned S3 Standard bucket using SSE-S3, an SQS Standard queue, and a dead-letter
queue with service-managed encryption. No server instances, RDS, NAT gateways,
customer-managed KMS keys, subscriptions, or public endpoints are created.

The bounded smoke test uses one subject, a sub-kilobyte S3 document, fewer than
100 queue/object API calls, and a small authority snapshot. Allow a conservative
USD 1 credit reserve for this tiny demonstration, not a price quotation or
measured bill. DynamoDB writes scale with snapshot size; do not run load tests
on this single-item research authority. There is no background polling daemon.
S3 storage and retained SQS payloads can continue consuming allowances/credits
after a run, although the test's document versions are erased. Keep the Free
plan and inspect AWS Settings > Billing before repeated experiments.

References checked 20 October 2026: [S3 pricing](https://aws.amazon.com/s3/pricing/),
[DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/),
[SQS pricing](https://aws.amazon.com/sqs/pricing/), and
[Free-plan behavior](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).

## Connect and verify

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/connect_aws.ps1 -Login
```

This refreshes browser login, checks the plan/credit reserve, reviews a change
set containing exactly the four expected additions, creates the stack, and runs
the synthetic live smoke test. It stops at the first failure. Existing healthy
project-tagged stacks are reused; foreign or unhealthy stacks are not changed.
The script leaves resources available after a successful run.

The runtime discovers resource names from CloudFormation outputs. It uses the
real DynamoDB authority, real S3 version-aware erasure adapter, and real SQS
producer/consumer. Operational records and the local index remain SQLite.
This is explicitly a hybrid connection, not a complete AWS-hosted deployment.
The smoke test proves version removal, stale S3 admission rejection, and an SQS
message rejected after erasure. Generated evidence stays under ignored
`evidence/private/`; it contains only synthetic test data but is not committed.

## Start the local API with the cloud core

Set the existing local authentication/evidence secrets privately, then:

```powershell
$env:RGX_ENV = 'local'
$env:RGX_RUNTIME = 'aws'
$env:RGX_AWS_PROFILE = 'rewindguard'
.venv\Scripts\python.exe -m backend.api.dev_server
```

The server stays bound to 127.0.0.1. Its erase API now covers the configured
cloud S3 adapter and local stores using the independent DynamoDB authority.
The SQS worker is bounded and explicitly invoked, not automatically running in
Lambda. Production authentication, RDS restore, Lambda/Step Functions hosting,
and KMS signatures remain separate work.

## Teardown

Stop the local API and workers. Verify the stack's `Project=rewindguard-x` tag.
In its dedicated DocumentBucket, remove only the synthetic object versions and
delete markers belonging to this demonstration. Check retained evidence before
deleting the authority: its tombstones must not be discarded while data is
still expected to be guarded. Then delete `rewindguard-x-core` through
CloudFormation and verify DELETE_COMPLETE. Never empty unrelated buckets.
The queues and authority table are dedicated synthetic project resources;
deleting the stack permanently removes their contents. Do not deploy with
personal or production data.
