"""Write docs/openapi.json so the front-end can generate a typed API client.

    python scripts/export_openapi.py
    # front-end:  npx openapi-typescript ../docs/openapi.json -o src/api/schema.d.ts
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import json
from pathlib import Path

from regimecast.api.main import app

out = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2))
print("wrote", out)
