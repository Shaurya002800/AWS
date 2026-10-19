"""Durable local task telemetry; no subject payloads or remote log shipping."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


class LocalAuditLog:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS task_audit "
                         "(task TEXT, state TEXT, duration REAL, generated_at TEXT)")

    def record(self, task, state, duration):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO task_audit VALUES(?,?,?,?)",
                         (task, state, duration, datetime.now(timezone.utc).isoformat()))

    def summary(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return [dict(zip(("task", "state", "count", "mean_seconds"), row)) for row in
                    conn.execute("SELECT task,state,count(*),avg(duration) FROM task_audit GROUP BY task,state")]
