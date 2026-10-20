"""Run a synthetic restore with no dependencies or network: python -m scripts.offline_demo."""
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict
from time import perf_counter
from backend.local_runtime import (create_local_candidate_snapshot, local_candidate_runtime,
                                   local_runtime, temporary_local_directory)
from contracts.adapter_protocol import SubjectScope


def demo():
    with temporary_local_directory() as directory:
        tasks = local_runtime(directory, secrets.token_bytes(32))
        scope = SubjectScope("synthetic", "example", 1)
        tasks.authority.register_subject(scope)
        for i, adapter in enumerate(tasks.adapters):
            adapter.put(scope, "document", 1, "synthetic content", f"seed-{i}")
        snapshot = create_local_candidate_snapshot(directory, "offline-restore")
        start = perf_counter()
        erased = tasks.erase({"scope": asdict(scope), "request_id": "erase-demo"})
        duration = perf_counter() - start
        with closing(sqlite3.connect(snapshot)) as conn:
            baseline_count = conn.execute("SELECT count(*) FROM local_data").fetchone()[0]
        restore = local_candidate_runtime(directory, "offline-restore", tasks.signer.key)
        approved = restore.validate({"candidate_id": "offline-restore", "tenant_id": scope.tenant_id})
        released = restore.release({"candidate_id": approved["candidate_id"], "scope_digest": approved["scope_digest"]})
        with closing(sqlite3.connect(snapshot)) as conn:
            protected_count = conn.execute("SELECT count(*) FROM local_data").fetchone()[0]
        return {"mode": "OFFLINE_SQLITE_ONLY", "baseline_raw_rows": baseline_count,
                "protected_raw_rows": protected_count, "erasure_seconds": duration,
                "state": erased["state"], "release_state": released["state"],
                "evidence_authenticated": tasks.signer.verify(erased["evidence"]),
                "limits": "One local SQLite restore example, not PostgreSQL or cloud evidence. "
                          "Ephemeral signing key is discarded after verification."}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
