"""Run a synthetic restore with no dependencies or network: python -m scripts.offline_demo."""
import json
import secrets
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from backend.local_runtime import local_runtime, temporary_local_directory
from backend.adapters.sqlite_local import SQLiteLocalAdapter
from backend.orchestration.handlers import WorkflowTasks
from contracts.adapter_protocol import SubjectScope


def demo():
    with temporary_local_directory() as directory:
        tasks = local_runtime(directory, secrets.token_bytes(32))
        scope = SubjectScope("synthetic", "example", 1)
        tasks.authority.register_subject(scope)
        for i, adapter in enumerate(tasks.adapters):
            adapter.put(scope, "document", 1, "synthetic content", f"seed-{i}")
        snapshot = Path(directory) / "candidate.sqlite3"
        with closing(sqlite3.connect(Path(directory) / "data.sqlite3")) as live, closing(sqlite3.connect(snapshot)) as candidate:
            live.backup(candidate)
        start = perf_counter()
        erased = tasks.erase({"scope": asdict(scope), "request_id": "erase-demo"})
        duration = perf_counter() - start
        with closing(sqlite3.connect(snapshot)) as conn:
            baseline_count = conn.execute("SELECT count(*) FROM local_data").fetchone()[0]
        adapters = [SQLiteLocalAdapter(snapshot, tasks.authority, a.name) for a in tasks.adapters]
        restore = WorkflowTasks(tasks.authority, adapters, tasks.signer)
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
