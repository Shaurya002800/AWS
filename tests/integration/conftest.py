import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from backend.adapters.postgres import migrate


@pytest.fixture
def database():
    base = os.getenv("RGX_TEST_POSTGRES_DSN")
    if not base:
        pytest.skip("Set RGX_TEST_POSTGRES_DSN for real PostgreSQL integration checks")
    schema = f"rgx_test_{uuid4().hex}"
    with psycopg.connect(base) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    dsn = make_conninfo(base, options=f"-c search_path={schema}")
    try:
        migrate(dsn)
        yield base, schema, dsn
    finally:
        with psycopg.connect(base) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
