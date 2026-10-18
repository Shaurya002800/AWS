# RewindGuard X

RewindGuard X is an AWS course project about preventing erased data from reappearing after a database restore, stale queue replay, S3 version recovery, or search-index rebuild. The owner is completing it solo. The local implementation contains EraseLock, PostgreSQL transaction journals, a versioned-object simulator, queue replay protection, a deterministic retrieval index, recovery gating, and a paired Resurrection Test Lab. The local API has a SQLite authority that survives restarts. Optional DynamoDB and S3 adapters and a core CloudFormation template are prepared offline. AWS deployment is pending the owner's zero-spend account check.

Start with the [technical blueprint](docs/RewindGuard_X_Complete_Technical_Blueprint.docx), [solo implementation scope](docs/SOLO_IMPLEMENTATION.md), [project configuration](docs/PROJECT_CONFIG.md), and [current status](docs/STATUS.md). The earlier team plan is retained as historical context. Contributor agents should also read [AGENTS.md](AGENTS.md).

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

To start a synthetic local API with subject `t01/p017` generation `4`:

```bash
export RGX_ENV=local
export RGX_LOCAL_API_TOKEN=choose-a-unique-local-value
python -m backend.api.dev_server
```

In a second terminal, send a request to `http://127.0.0.1:8000/v1/erase-requests` with `Authorization: Bearer <your-local-value>`, `X-RGX-Tenant: t01`, `X-RGX-Subject: p017`, and JSON body:

```json
{"tenant_id":"t01","subject_id":"p017","subject_generation":4,"request_id":"req-1"}
```

Open `http://127.0.0.1:8000/ui` for the local lab dashboard. Set `RGX_TEST_POSTGRES_DSN` before starting the server to run lab scenarios from the dashboard. The erase endpoint closes write admission and returns `ADMISSION_CLOSED`; the lab CLI runs the full datastore workflow. The token plus identity headers are a **local demo mechanism**, not production authentication; the API rejects requests unless `RGX_ENV=local`. The server authority persists in `.local-authority/authority.sqlite3` by default, outside the recoverable PostgreSQL database. Set `RGX_AUTHORITY_DB` to use a different local path. The generated API contract is in `contracts/api.openapi.json`.

## Contributor boundary

The owner implements the entire project. Local adapter evidence is based on verification reads, not merely successful invocation. The object version store is an in-memory S3 model, not a claim that real S3 has been deployed. The evidence certificate has a SHA-256 integrity digest and is explicitly marked `UNSIGNED_LOCAL`; AWS KMS signing remains a later cloud milestone. See the [evaluation report](docs/EVALUATION.md) and [AWS readiness runbook](docs/AWS_READINESS.md) for measured results, deployment gates, service status, and limitations.
