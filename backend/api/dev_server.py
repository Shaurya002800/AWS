"""Run the synthetic local API with a restart-safe authority file."""

import os

import uvicorn

from contracts.adapter_protocol import SubjectScope
from backend.eraselock.domain import NotFound
from backend.eraselock.sqlite_authority import SQLiteAuthority
from backend.api.app import create_app
from backend.local_runtime import local_runtime


def main() -> None:
    if os.environ.get("RGX_ENV") != "local":
        raise SystemExit("Set RGX_ENV=local before starting")
    key = os.environ.get("RGX_LOCAL_EVIDENCE_KEY", "").encode()
    tasks = local_runtime(os.environ.get("RGX_LOCAL_DIRECTORY", ".local-authority"), key)
    authority = tasks.authority
    scope = SubjectScope("t01", "p017", 4)
    try:
        authority.state(scope)
    except NotFound:
        authority.register_subject(scope)
    uvicorn.run(create_app(authority, adapters=tasks.adapters, signer=tasks.signer, tasks=tasks),
                host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
