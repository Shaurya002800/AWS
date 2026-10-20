# RewindGuard X

[![Local checks](https://github.com/Shaurya002800/AWS/actions/workflows/local-checks.yml/badge.svg?branch=main)](https://github.com/Shaurya002800/AWS/actions/workflows/local-checks.yml)

## Current cloud progress — 20 October 2026

[Open the notebook-style cloud demo ↗](https://es3l5u1w1c.execute-api.ap-southeast-2.amazonaws.com/ui) · [UI design and checks](docs/UI_REDESIGN.md)

| Component | Verified status |
| --- | --- |
| DynamoDB, S3, SQS | Deployed and live smoke checks passed |
| Private RDS, Lambda, Step Functions, Cognito, KMS, IAM, monitoring | Deployed in ap-southeast-2 |
| Real snapshot recovery evaluation | All 15 checks passed; target profile/document/index counts changed from 1/1/1 to 0/0/0 |
| Signed evidence | Real KMS verification and tamper rejection passed |
| Cost controls | Free plan active; no USD 25 minimum; both databases confirmed stopped; disposable snapshots removed |
| Local test suite | 79 passed + 6 frontend checks; 16 PostgreSQL tests require a local DSN and are exercised by CI's PostgreSQL service |

See the [actual cloud evaluation](docs/FINAL_CLOUD_EVALUATION.md), [demo start/sign-in/stop runbook](docs/CLOUD_DEMO_RUNBOOK.md), and [detailed status](docs/STATUS.md).
Stopped databases must be resumed before a live demo. Retained storage and signing resources can still consume credits. These are synthetic research results, not production certification.

**Current offline checkpoint:** see [OFFLINE_COMPLETION.md](docs/OFFLINE_COMPLETION.md).
Run `python -m unittest discover -s tests/offline -v` and
`python -m scripts.offline_demo` without installing packages or using AWS.
The erase API now runs deletion/verification with its configured adapters;
local sessions, evidence authentication, task audit, and recovery checks are
implemented. The setup instructions below describe the original PostgreSQL
lab; use the checkpoint document for the updated API startup and current limits.

RewindGuard X is an AWS course project about preventing erased data from reappearing after a database restore, stale queue replay, S3 version recovery, or search-index rebuild. The owner is completing it solo. The local implementation contains EraseLock, PostgreSQL transaction journals, a versioned-object simulator, queue replay protection, a deterministic retrieval index, recovery gating, and a paired Resurrection Test Lab. The local API has a SQLite authority that survives restarts. The DynamoDB, S3 and SQS core and the expanded private cloud application are now deployed; the cloud evaluation linked above records the observed results.

Start with the [technical blueprint](docs/RewindGuard_X_Complete_Technical_Blueprint.docx), [solo implementation scope](docs/SOLO_IMPLEMENTATION.md), [project configuration](docs/PROJECT_CONFIG.md), and [current status](docs/STATUS.md). The earlier team plan is retained as historical context. Contributor agents should also read [AGENTS.md](AGENTS.md).

For a new AWS project, [AWS first-time setup](docs/AWS_FIRST_TIME_SETUP.md) preserves the spending gate. The current project is already deployed; use the cloud demo runbook above to resume and stop it.

The working branch is `main` at `https://github.com/Shaurya002800/AWS.git`; the owner requested direct pushes to it. Only the owner connects to the AWS account. The project has a zero out-of-pocket spending rule; all local work runs without AWS credentials.

## Run the current local slice

Python 3.11 or newer and PostgreSQL 15 are required for the complete local lab. Start PostgreSQL with Docker Compose, or on a Mac with Homebrew PostgreSQL 15 use `scripts/local_postgres.sh start`. Neither path uses AWS credentials.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
docker compose up -d postgres
export RGX_TEST_POSTGRES_DSN='postgresql://rgx:rgx-local-only@127.0.0.1:55432/rgx'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m lab.cli run --scenario old_database_recovery --mode baseline
python -m lab.cli run --scenario old_database_recovery --mode protected
python -m lab.cli suite --seeds 42 43
```

On this Mac, when using the local binary instead of Docker, run `scripts/local_postgres.sh start` and set `RGX_TEST_POSTGRES_DSN="postgresql://$(whoami)@127.0.0.1:55432/postgres"`. Run `scripts/local_postgres.sh stop` when finished. `python -m lab.cli list` shows all four scenarios. Each run creates temporary PostgreSQL schemas and removes them afterward; `--output path.json` saves a result outside the ignored `lab/reports/` directory if needed.

To start the updated synthetic local API with subject `t01/p017` generation `4`, set two distinct random local keys. On macOS/Linux:

```bash
export RGX_ENV=local
export RGX_AUTH_MODE=signed
export RGX_LOCAL_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export RGX_LOCAL_EVIDENCE_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python -m scripts.local_token --tenant t01 --subject p017
python -m backend.api.dev_server
```

In a second terminal, send a request to `http://127.0.0.1:8000/v1/erase-requests` with `Authorization: Bearer <printed-token>` and JSON body:

```json
{"tenant_id":"t01","subject_id":"p017","subject_generation":4,"request_id":"req-1"}
```

Open `http://127.0.0.1:8000/ui` for the local lab dashboard. Set `RGX_TEST_POSTGRES_DSN` before starting the server to run lab scenarios from the dashboard. The erase endpoint runs its configured local erasure workflow and returns a verified or held outcome. Signed local sessions are **demo authentication**, not production OIDC; the API rejects requests unless `RGX_ENV=local`. The server stores local authority and adapter data under `.local-authority/` by default. Set `RGX_LOCAL_DIRECTORY` to use a different local path. The generated API contract is in `contracts/api.openapi.json`.

## Contributor boundary

The owner implements the entire project. Local adapter evidence is based on verification reads, not merely successful invocation. The object version store is an in-memory S3 model, not a claim that real S3 has been deployed. The inner evidence certificate has a SHA-256 digest and remains marked `UNSIGNED_LOCAL`; the updated local runtime wraps it in an HMAC-SHA256 authenticated envelope. KMS/public-key signing remains pending. See the [evaluation report](docs/EVALUATION.md) and [AWS readiness runbook](docs/AWS_READINESS.md) for measured results, deployment gates, service status, and limitations.
