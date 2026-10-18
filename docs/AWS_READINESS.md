# AWS readiness and zero-spend gate

Updated 19 October 2026. The owner asked to finish offline work first. No AWS resources have been deployed or connected. The following steps are for the later owner-led setup; do not run a deploy command before the account plan, Region, active allowances/credits, and resource estimate are verified.

## Service status

| AWS service | Intended role | Current evidence |
| --- | --- | --- |
| DynamoDB | Independent EraseLock authority | Conditional-write adapter tested with a fake client; no live call |
| S3 | Versioned synthetic documents | Version-aware adapter tested with a fake client; no live call |
| SQS and dead-letter queue | Delayed update replay | Local simulator tested; core queues defined in CloudFormation; no live consumer |
| RDS for PostgreSQL | Operational records and restore candidate | Real local PostgreSQL integration; no RDS instance or snapshot |
| Lambda, Step Functions, EventBridge | Cloud workers and orchestration | Design in the blueprint; no deployed implementation |
| IAM, CloudWatch, KMS | Access control, telemetry, signatures | No deployed roles, alarms, or KMS signatures |

The file `infra/core.json` is an **opt-in partial core template**, not an end-to-end deployment. It defines DynamoDB, S3 and SQS resources only. The repo does not currently contain a production API, production identity provider, managed restore switch, or full cloud state machine. A course demonstration can present the complete local experiment and accurately state which AWS components were prepared versus deployed.

## Owner setup sequence, later

1. In the AWS Billing console, record whether the account is on the Free or Paid plan, active credit balance and expiry, and current Free Tier usage. Do not put account identifiers or billing details in Git.
2. Choose one Region and check service availability and current prices for every proposed resource there. Confirm the zero out-of-pocket rule on the actual account, including existing usage. A Free plan may restrict services; a Paid plan may incur charges beyond credits or allowances. Do not assume that a zero-dollar budget prevents charges.
3. Enable billing and Free Tier usage alerts. Budget alerts are monitoring, not a hard spending cap.
4. If and only if the estimate and account eligibility allow zero out-of-pocket cost, use a dedicated least-privilege identity and validate the template with `aws cloudformation validate-template --template-body file://infra/core.json --region <chosen-region>`. Review the resulting change set before creating resources.
5. Use only synthetic records. Measure request counts and storage, keep the experiment short, and inspect billing/credit usage during the run. No live RDS, KMS, CloudFront or other resource is implied by the partial template.
6. Empty **only the dedicated project bucket's versions and delete markers**, delete the stack, then verify the bucket, queues, table and stack are gone. Versioned S3 buckets do not become empty when an ordinary delete marker is created. Record redacted service IDs, timestamps and evidence locally after deployment.

Current AWS guidance distinguishes Free and Paid account plans, and says Free plans have service restrictions while Paid plans can charge after credits or allowances. Verify the actual account at setup time: [AWS Free Tier](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html), [plan comparison](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html), and [Free Tier usage tracking](https://docs.aws.amazon.com/us_en/awsaccountbilling/latest/aboutv2/tracking-free-tier-usage.html).
