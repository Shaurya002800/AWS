# Cloud demo runbook

The hosted research demo is at https://es3l5u1w1c.execute-api.ap-southeast-2.amazonaws.com/ui.
Sign-in is required for data operations. Public registration is disabled.

## Owner operator setup

From the repository terminal, create an operator once. The command prompts for a password locally and does not send invitations or print the password:

~~~powershell
.venv/Scripts/python.exe -m scripts.create_operator --username suhani
~~~

Sign in on the hosted page with that username and password. The browser uses the Cognito authorization-code flow with PKCE.

## Before a demonstration

~~~powershell
.venv/Scripts/python.exe -m scripts.start_cloud_demo
~~~

This checks that the project is FREE/ACTIVE with positive credits, resumes the active private RDS database, and extends its two-hour idle safety window. There is no USD 25 minimum or reservation. Startup can take several minutes. Do not upgrade the project to Paid.

Use the page to create a synthetic profile and run the snapshot/erase/restore workflow. An old snapshot is restored into a private isolated candidate; reconciliation and signed evidence checks precede publication. Workflow status is polled by the UI. Keep the browser page open during the demonstration.

## Immediately afterward

~~~powershell
.venv/Scripts/python.exe -m scripts.stop_cloud_demo
~~~

This stops available project-tagged demo databases, removes disposable synthetic test snapshots, and limits database log retention to one day. It preserves the database data for the next demonstration. Storage, secrets, signing keys, and logs may still consume credits while retained. For the lowest long-term usage, remove the application resources once the final demonstration is no longer needed. The source code and signed evaluation evidence can be retained separately.

The scheduled safety cleanup also stops expired serving databases and deletes expired unused candidates. Stopped RDS databases can restart automatically after seven days; the cleanup schedule is a fallback, not a permanent zero-usage guarantee.

## Evaluation evidence

The evaluator creates and deletes a temporary synthetic Cognito operator without notifications. Raw outputs are ignored under evidence/private. FINAL_CLOUD_EVALUATION.md is generated only by the real-cloud evaluator and records observed checks and delayed-credit-measurement limitations. A research demonstration is not production security certification.
