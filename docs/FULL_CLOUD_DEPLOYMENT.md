# Full cloud deployment checkpoint

Deployed on 20 October 2026 after explicit direct-Python authorization. The core DynamoDB/S3/SQS stack is live and verified, and the expanded stack reached CREATE_COMPLETE. Real cloud evaluation passed all 15 checks. Both databases are confirmed stopped and disposable snapshots removed; see STATUS.md and FINAL_CLOUD_EVALUATION.md.

## Prepared components

- Private encrypted PostgreSQL 17 RDS with IAM database authentication and no public endpoint or NAT gateway.
- Separate isolated snapshot restore, reconciliation, guarded publication, and an atomic authority check before changing the active database pointer.
- Five runtime Lambda functions plus a temporary bootstrap function and a bounded Step Functions snapshot/erase/restore/validate/release workflow.
- Cognito authorization code sign-in with PKCE, API Gateway JWT enforcement, tenant/subject authorization, and scoped service roles. This is a research deployment; production readiness still requires live security review.
- KMS asymmetric evidence signing and verification, CloudWatch logs/alarms, throttling, and scheduled idle cleanup.
- Real-cloud evaluator covering synthetic data, authentication rejection, snapshot contents, reconciliation, publication, KMS tampering, and small-sample request latency. It generates FINAL_CLOUD_EVALUATION.md after a completed real run, with each check’s observed result.

## Cost boundary

The deployer and demo startup require a verified ACTIVE FREE plan and positive remaining credits, without a fixed reserve. Neither upgrades the project. All regional resources use ap-southeast-2. Credits are consumed by RDS, storage, KMS, API, logging, and other metered services; a new account alone does not establish eligibility.

Use one short synthetic evaluation and stop both databases immediately afterward. Automated backups are disabled; the explicit test snapshot still exercises backup restoration. Logs expire after one day. The synthetic application database is deleted without a retained final snapshot on stack teardown. Database expiry tags target two hours and cleanup runs every 30 minutes. Idle serving databases are stopped, unused expired candidates and synthetic snapshots are deleted. Stopped database storage, keys, secrets, and logs can continue consuming credits. RDS can restart stopped instances automatically, so these controls are not a permanent zero-usage guarantee. Recheck project status and delete the application stack after the final demo if it is no longer needed. Never switch to Paid to bypass a deployment rejection.

## Owner commands for deployment and use

Run from the repository, using the existing rewindguard CLI profile and project login cache:

```powershell
.venv/Scripts/python.exe -m scripts.deploy_application  # Initial deployment only
.venv/Scripts/python.exe -m scripts.start_cloud_demo
.venv/Scripts/python.exe -m scripts.evaluate_application  # Always stops databases afterward
.venv/Scripts/python.exe -m scripts.create_operator --username YOUR_OPERATOR_NAME
```

The evaluator creates a temporary synthetic Cognito user without sending messages, then deletes it. Credentials remain in memory. Private cloud outputs and evaluation evidence stay under ignored evidence/private. Operator creation prompts for a password locally. Resume a stopped demo with `python -m scripts.start_cloud_demo` using the project environment.

## Current verification and limitations

The template passes local cfn-lint for ap-southeast-2. Local unit tests cover JWT authorization, tenant boundaries, KMS envelope tampering, private network separation, conditional master-secret bootstrap access, workflow holds, and preservation of the active restored database during cleanup. The real PostgreSQL tests require a local test DSN and are skipped in this environment.

The real-cloud evaluation passed, including KMS verification and isolated RDS recovery. No finalized bill or production certification is claimed. The existing live core smoke test uses local operational records and a local HMAC signature. Do not substitute those results for full cloud evaluation.
