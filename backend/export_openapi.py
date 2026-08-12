"""Write the API's OpenAPI schema to backend/openapi.json.

The frontend generates its DTO types from this file (`npm run gen:types`), so it
must match the live routes. Building from a bare app with just `api.router`
avoids main.py's db-init / static-mount side effects.

    uv run python export_openapi.py
"""

import json
from pathlib import Path

from fastapi import FastAPI

import api

app = FastAPI(title="Stronghold API")
app.include_router(api.router)

out = Path(__file__).resolve().parent / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
