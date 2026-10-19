"""Print local task counts and durations without contacting a monitoring service."""
import argparse
import json
from pathlib import Path
from backend.eraselock.local_monitoring import LocalAuditLog

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path(".local-authority"))
    args = parser.parse_args()
    path = args.directory / "audit.sqlite3"
    if not path.is_file():
        raise SystemExit("No local audit database exists yet")
    print(json.dumps(LocalAuditLog(path).summary(), indent=2))
