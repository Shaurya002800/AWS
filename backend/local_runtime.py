"""Construct the offline runtime. This module cannot initialize an AWS client."""
from pathlib import Path
from contextlib import closing, contextmanager
from uuid import uuid4
import os
import re
import shutil
import sqlite3
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


def _candidate_path(directory, candidate_id):
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", candidate_id):
        raise ValueError("Unsafe candidate ID")
    root = Path(directory).resolve()
    return root / "candidates" / f"{candidate_id}.sqlite3"


def create_local_candidate_snapshot(directory, candidate_id):
    """Copy the current synthetic store into a separate, immutable-named candidate."""
    root = Path(directory).resolve()
    source = root / "data.sqlite3"
    target = _candidate_path(root, candidate_id)
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("Live local store is missing or unsafe")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ValueError("Candidate directory is a symlink")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(target)) as candidate:
            live.backup(candidate)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def local_candidate_runtime(directory, candidate_id, evidence_key):
    """Bind recovery tasks to an existing isolated local snapshot by ID."""
    root = Path(directory).resolve()
    candidate = _candidate_path(root, candidate_id)
    if candidate.parent.is_symlink() or not candidate.is_file() or candidate.is_symlink():
        raise FileNotFoundError("Candidate snapshot is missing or unsafe")
    authority = SQLiteAuthority(root / "authority.sqlite3")
    adapters = [SQLiteLocalAdapter(candidate, authority, name) for name in
                ("local-records", "local-object-versions", "local-index")]
    return WorkflowTasks(authority, adapters, LocalEvidenceSigner(evidence_key),
                         LocalAuditLog(root / "audit.sqlite3"), candidate_id=candidate_id)


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
