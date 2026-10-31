"""Write the deterministic OpenAPI contract to backend/openapi.json."""

import json
from pathlib import Path

from explorai_backend.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    OPENAPI_PATH.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
