"""Construct the offline runtime. This module cannot initialize an AWS client."""
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4
import shutil
from backend.adapters.sqlite_local import SQLiteLocalAdapter
from backend.eraselock.sqlite_authority import SQLiteAuthority
from backend.eraselock.local_security import LocalEvidenceSigner
from backend.orchestration.handlers import WorkflowTasks
from backend.eraselock.local_monitoring import LocalAuditLog


def local_runtime(directory, evidence_key):
    root = Path(directory)
    authority = SQLiteAuthority(root / "authority.sqlite3")
    adapters = [SQLiteLocalAdapter(root / "data.sqlite3", authority, name) for name in
                ("local-records", "local-object-versions", "local-index")]
    return WorkflowTasks(authority, adapters, LocalEvidenceSigner(evidence_key),
                         LocalAuditLog(root / "audit.sqlite3"))


@contextmanager
def temporary_local_directory():
    """Workspace temp directory compatible with restricted Windows tokens."""
    parent = (Path.cwd() / ".local-authority" / "test-runs").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / uuid4().hex
    root.mkdir()
    try:
        yield root
    finally:
        if root.resolve().parent != parent or root.is_symlink():
            raise RuntimeError("Unexpected temporary directory target")
        shutil.rmtree(root)
