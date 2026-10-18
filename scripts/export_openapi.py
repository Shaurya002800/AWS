"""Regenerate the checked-in API contract from the FastAPI application."""

import json
from pathlib import Path

from backend.api.app import create_app


root = Path(__file__).resolve().parents[1]
output = root / "contracts" / "api.openapi.json"
output.write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")
print(output)
