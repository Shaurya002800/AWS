# RewindGuard X

RewindGuard X is a three-person AWS course project about preventing erased data from reappearing after a database restore, stale queue replay, S3 version recovery, or search-index rebuild. The first local EraseLock vertical slice now contains the state machine, process-local authority, permits, epoch-bound recovery decisions, and a local-only API. PostgreSQL/S3 adapters and AWS deployment are still pending.

Start with the [technical blueprint](docs/RewindGuard_X_Complete_Technical_Blueprint.docx), [team execution plan](docs/TEAM_EXECUTION_PLAN.md), [project configuration](docs/PROJECT_CONFIG.md), and [current status](docs/STATUS.md). Contributor agents should also read [AGENTS.md](AGENTS.md).

The integration branch is `main` at `https://github.com/Shaurya002800/AWS.git`. Development happens on reviewed feature branches. Only Member 1 connects to the AWS account. The project has a zero out-of-pocket spending rule; all local work runs without AWS credentials.

## Run the current local slice

Python 3.11 or newer is required. This slice does not need Docker or AWS credentials.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```

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

The endpoint closes write admission and returns `ADMISSION_CLOSED`. It does not yet remediate any datastore. The token plus identity headers are a **local demo mechanism**, not production authentication; the API rejects requests unless `RGX_ENV=local`. Its process-local authority loses state when restarted. The generated API contract is in `contracts/api.openapi.json`.

## Contributor boundary

Member 1 owns `backend/eraselock/`, `backend/api/`, contracts, and future infrastructure. Member 2 can implement adapters and workers against `contracts/adapter_protocol.py` and `contracts/authority_protocol.py` without AWS access. The adapter's result must be based on a real verification read, not merely successful invocation. Member 3 verifies from a fresh clone and records the tested scope.
