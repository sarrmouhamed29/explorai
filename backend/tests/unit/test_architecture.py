"""Structural rules that keep the row-level-security boundary intact."""

from pathlib import Path

from explorai_backend.main import create_app

SRC = Path(__file__).resolve().parents[2] / "src" / "explorai_backend"
FORBIDDEN = ("sessionmaker(", "Session(", ".begin(")
ALLOWED_FILE = "deps.py"


def test_no_backend_module_opens_sessions_outside_scoped_session() -> None:
    offenders = [
        f"{path.name}: {token}"
        for path in SRC.rglob("*.py")
        if path.name != ALLOWED_FILE
        for token in FORBIDDEN
        if token in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_business_routes_are_versioned() -> None:
    paths = create_app().openapi()["paths"]
    business = [path for path in paths if not path.startswith("/health")]

    assert all(path.startswith("/api/v1") for path in business)
