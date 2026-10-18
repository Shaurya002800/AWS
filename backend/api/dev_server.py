"""Run an isolated synthetic local API for the Week 1 demonstration."""

import os

import uvicorn

from contracts.adapter_protocol import SubjectScope
from backend.eraselock.authority import InMemoryAuthority
from backend.api.app import create_app


def main() -> None:
    if os.environ.get("RGX_ENV") != "local" or not os.environ.get("RGX_LOCAL_API_TOKEN"):
        raise SystemExit("Set RGX_ENV=local and RGX_LOCAL_API_TOKEN before starting")
    authority = InMemoryAuthority()
    authority.register_subject(SubjectScope("t01", "p017", 4))
    uvicorn.run(create_app(authority), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
