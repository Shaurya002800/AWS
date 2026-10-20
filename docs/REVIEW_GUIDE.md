# Review guide — 20 October 2026

[Open the live demo](https://es3l5u1w1c.execute-api.ap-southeast-2.amazonaws.com/ui).
The implemented synthetic course demo is ready for review. Authentication, private
PostgreSQL, Lambda, Step Functions, DynamoDB, S3, SQS, KMS and monitoring are deployed
in ap-southeast-2. This is a research demonstration, not production certification.

## A short presentation

1. Explain the problem: restoring an old backup can resurrect previously erased data.
2. Open the demo and explain the three steps: sign in, create synthetic data, test recovery.
3. Open “Review the completed cloud evaluation”. The real recovery run passed all 15
   checks: the restored profile/document/index counts were 1/1/1 and became 0/0/0
   after reconciliation. The candidate remained gated until verification, then RELEASED.
   Real KMS signature verification and tamper rejection passed.
4. Explain the architecture: the erasure ledger survives outside the restored database;
   isolated restored candidates are reconciled before publication. Object versions,
   derived index entries, and stale events have separate guards.
5. For a live interaction, sign in, create a synthetic profile, and inspect the response.
   A fresh full recovery run creates another temporary RDS candidate and takes several
   minutes (472.34 seconds in the measured run). Use the completed evidence for a short
   review; a new run is optional. Evidence panels show actual results, not seeded metrics.
6. Download run JSON if desired, then sign out. Signing out does not cancel a running workflow.

## Private first login

The owner login is prepared. Its username and temporary password are only in
ignored local file **evidence/private/review-login.txt**. Open it locally, sign in,
and choose your own password when Cognito prompts. The first-login password-change
challenge was verified. No credentials are in GitHub. Do not share the owner login
with reviewers. The temporary password expires about 24 hours after creation;
additional operator setup is documented in CLOUD_DEMO_RUNBOOK.md.

## Review window and shutdown

From the repository terminal:

~~~powershell
.venv/Scripts/python.exe -m scripts.start_cloud_demo --minutes 30
~~~

Only the approved serving database is resumed. Its original source remains stopped.
The default window is 30 minutes after startup; scheduled cleanup checks every
30 minutes, so shutdown can occur up to 30 minutes after expiry. Startup takes
several minutes. Rerun before a later review. Stop immediately when finished:

~~~powershell
.venv/Scripts/python.exe -m scripts.stop_cloud_demo
~~~

The guard requires FREE/ACTIVE with positive credits, without a USD 25 reservation.
No paid upgrade is performed. Retained storage, secrets, signing keys and logs can
consume credits even with stopped databases; metering is delayed. This is not a
promise of zero credit usage.

## Verified results and boundaries

- Full snapshot/erase/restore evaluation: all 15 checks passed; see FINAL_CLOUD_EVALUATION.md.
- Current serving database, 13:36 UTC: all nine additional live checks passed:
  authenticated operator; real seed/index/profile read; verified erasure; valid KMS
  signature; tamper rejection; erased profile unavailable; cross-tenant denial;
  no remaining erased object versions. Temporary test login removed afterward.
- The current serving check created no additional database or snapshot. It is a
  separate check, not a repeat of the full recovery experiment.
- Local PostgreSQL tests require a DSN; GitHub CI supplies PostgreSQL. UI visual
  checks used DOM/layout; browser screenshot capture was unavailable.
- Private evidence remains under evidence/private. Public reports describe observed
  synthetic results and measurement limitations. No production-load or certification claim.
