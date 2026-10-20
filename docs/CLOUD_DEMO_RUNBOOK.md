# Cloud demo runbook

The hosted research demo is at https://es3l5u1w1c.execute-api.ap-southeast-2.amazonaws.com/ui.
Sign-in is required for data operations. Public registration is disabled.

## Owner operator setup

A private review-owner login has been prepared. Open ignored local file evidence/private/review-login.txt and complete the required first-login password change. Never commit or share that file. For an additional operator, from the repository terminal, create one once. The command prompts for a password locally and does not send invitations or print the password:

~~~powershell
.venv/Scripts/python.exe -m scripts.create_operator --username suhani
~~~

Sign in on the hosted page with that username and password. The browser uses the Cognito authorization-code flow with PKCE.

## Before a demonstration

~~~powershell
.venv/Scripts/python.exe -m scripts.start_cloud_demo
~~~

This checks that the project is FREE/ACTIVE with positive credits, resumes the active private RDS database, and sets a 30-minute review window after startup. Scheduled cleanup runs every 30 minutes, so it can stop the database up to 30 minutes after expiry. Only the approved serving database is resumed; the original source stays stopped. Use --minutes 15 through --minutes 120 to change the window. There is no USD 25 minimum or reservation. Startup can take several minutes. Do not upgrade the project to Paid.

Use the page to create a synthetic profile and run the snapshot/erase/restore workflow. An old snapshot is restored into a private isolated candidate; reconciliation and signed evidence checks precede publication. Workflow status is polled by the UI. Keep the browser page open during the demonstration.

## Immediately afterward

~~~powershell
.venv/Scripts/python.exe -m scripts.stop_cloud_demo
~~~

This stops available project-tagged demo databases, removes disposable synthetic test snapshots, and limits database log retention to one day. It preserves the database data for the next demonstration. Storage, secrets, signing keys, and logs may still consume credits while retained. For the lowest long-term usage, remove the application resources once the final demonstration is no longer needed. The source code and signed evaluation evidence can be retained separately.

The scheduled safety cleanup also stops expired serving databases and deletes expired unused candidates. Stopped RDS databases can restart automatically after seven days; the cleanup schedule is a fallback, not a permanent zero-usage guarantee.

## Evaluation evidence

The evaluator creates and deletes a temporary synthetic Cognito operator without notifications. Raw outputs are ignored under evidence/private. FINAL_CLOUD_EVALUATION.md is generated only by the real-cloud evaluator and records observed checks and delayed-credit-measurement limitations. A research demonstration is not production security certification.

## Free plan restore capacity

Stopped RDS instances still occupy instance slots. This project encountered a two-instance
Free plan limit on the owner’s repeat recovery run. The capacity guard now holds before
snapshot/erasure if both slots are occupied. Do not upgrade to Paid.

The superseded synthetic original database can be previewed with
`python -m scripts.retire_original_database`. It preserves the published candidate.
Only after explicit owner confirmation, `--execute` updates CloudFormation to remove
the original permanently without a final snapshot. The retained-database parameter
defaults to true, so ordinary deployments preserve it. Existing completed evidence
is in FINAL_CLOUD_EVALUATION.md; a held run must never be presented as a success.

After successful publication, cleanup reclaims the previously published disposable
candidate once it is no longer ACTIVE, including if it is stopped. The new serving
candidate is preserved. Deletion can take several minutes; wait for the slot to
be released before another recovery. Unexpired isolated candidates are preserved.
The original rgx-live was retired through CloudFormation after owner approval.
