"""Durable synthetic local stores; not an S3, PostgreSQL, or AI emulator.

Each adapter has a fixed namespace. Data and its operation journal commit
together; the independent authority lives in a different SQLite file.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from backend.eraselock.domain import Conflict, PermitState
from contracts.adapter_protocol import AdapterEvidence, CheckResult, Location, SubjectScope


class SQLiteLocalAdapter:
    def __init__(self, path: str | Path, authority, name: str):
        if name not in {"local-records", "local-object-versions", "local-index"}:
            raise ValueError("Unknown local adapter")
        self.path, self.authority, self.name = Path(path), authority, name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_data (adapter TEXT, tenant TEXT, subject TEXT, "
                         "generation INTEGER, locator TEXT, version INTEGER, content TEXT, "
                         "PRIMARY KEY(adapter,tenant,subject,generation,locator,version))")
            conn.execute("CREATE TABLE IF NOT EXISTS local_journal (operation TEXT PRIMARY KEY, digest TEXT)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _key(self, scope):
        return self.name, scope.tenant_id, scope.subject_id, scope.subject_generation

    def put(self, scope: SubjectScope, locator: str, version: int, content: str, operation_id: str):
        if not locator or version < 1 or not operation_id:
            raise ValueError("Locator, version, and operation ID required")
        digest = sha256(json.dumps([*self._key(scope), locator, version, content]).encode()).hexdigest()
        permit = self.authority.acquire_permit(scope, operation_id)
        # A crash before permit closure deliberately leaves an unresolved permit.
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT digest FROM local_journal WHERE operation=?", (operation_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise Conflict("Operation payload changed")
            else:
                if permit.state != PermitState.OPEN:
                    raise Conflict("Cannot reuse an expired or closed write permit")
                latest = conn.execute("SELECT MAX(version) FROM local_data WHERE adapter=? AND tenant=? "
                                      "AND subject=? AND generation=? AND locator=?",
                                      (*self._key(scope), locator)).fetchone()[0]
                if latest is not None and version <= latest:
                    self.authority.close_permit(permit.permit_id, outcome="FAILED", journal_confirmed=True)
                    raise Conflict("Stale source version")
                conn.execute("INSERT INTO local_data VALUES(?,?,?,?,?,?,?)",
                             (*self._key(scope), locator, version, content))
                conn.execute("INSERT INTO local_journal VALUES(?,?)", (operation_id, digest))
        self.authority.close_permit(permit.permit_id, outcome="COMMITTED", journal_confirmed=True)

    def public_read(self, scope):
        if not self.authority.may_read(scope):
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT locator,version,content FROM local_data WHERE adapter=? "
                                "AND tenant=? AND subject=? AND generation=?", self._key(scope)).fetchall()
        return rows if self.authority.may_read(scope) else []

    def reconcile_committed(self, scope):
        """Only durable positive journal evidence may resolve a crashed write."""
        for permit in self.authority.unresolved_permits(scope):
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT digest FROM local_journal WHERE operation=?",
                                   (permit.operation_id,)).fetchone()
            if row is not None:
                self.authority.close_permit(permit.permit_id, outcome="COMMITTED", journal_confirmed=True)

    def discover(self, scope):
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT locator,version FROM local_data WHERE adapter=? "
                                "AND tenant=? AND subject=? AND generation=?", self._key(scope)).fetchall()
        return [Location(self.name, row[0], str(row[1])) for row in rows]

    def remediate(self, scope, decision_id):
        if not decision_id:
            raise ValueError("Decision required")
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM local_data WHERE adapter=? AND tenant=? AND subject=? AND generation=?",
                         self._key(scope))

    def verify(self, scope, authority_epoch):
        count = len(self.discover(scope))
        return AdapterEvidence(self.name, scope, authority_epoch,
                               CheckResult.FAIL if count else CheckResult.PASS,
                               f"local-{self.name}-{authority_epoch}", count, count)
