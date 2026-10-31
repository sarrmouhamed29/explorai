import json
from pathlib import Path

from explorai_backend.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def test_committed_openapi_matches_app() -> None:
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert committed == create_app().openapi()


def test_openapi_declares_health_paths() -> None:
    paths = create_app().openapi()["paths"]
    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert {"200", "503"} <= set(paths["/health/ready"]["get"]["responses"])
