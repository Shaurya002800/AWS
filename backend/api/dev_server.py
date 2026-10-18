"""Run the synthetic local API with a restart-safe authority file."""

import os

import uvicorn

from contracts.adapter_protocol import SubjectScope
from backend.eraselock.domain import NotFound
from backend.eraselock.sqlite_authority import SQLiteAuthority
from backend.api.app import create_app


def main() -> None:
    if os.environ.get("RGX_ENV") != "local" or not os.environ.get("RGX_LOCAL_API_TOKEN"):
        raise SystemExit("Set RGX_ENV=local and RGX_LOCAL_API_TOKEN before starting")
    authority = SQLiteAuthority(os.environ.get("RGX_AUTHORITY_DB", ".local-authority/authority.sqlite3"))
    scope = SubjectScope("t01", "p017", 4)
    try:
        authority.state(scope)
    except NotFound:
        authority.register_subject(scope)
    uvicorn.run(create_app(authority), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
