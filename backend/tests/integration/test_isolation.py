"""Isolation suite: user A can never see, read, list or delete user B's documents.

The invariant is checked across every route rather than route by route, and
once more at the database level, without the repository's explicit filter.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import Document
from explorai_db.rls import scoped_session
from integration.helpers import add_document, login_as

pytestmark = pytest.mark.integration

API = "/api/v1"
DOCUMENTS = f"{API}/documents"
BASE = datetime(2026, 1, 1, tzinfo=UTC)

# Every document route. Adding a route must change this set, which forces the
# author to extend the sweeps below.
EXPECTED_DOCUMENT_ROUTES = {
    ("GET", f"{DOCUMENTS}"),
    ("POST", f"{DOCUMENTS}"),
    ("GET", f"{DOCUMENTS}/{{document_id}}"),
    ("DELETE", f"{DOCUMENTS}/{{document_id}}"),
    ("GET", f"{DOCUMENTS}/{{document_id}}/sections"),
}
# Routes that need no session. Logout is idempotent by design: without a cookie
# it only clears the cookie and touches no data.
PUBLIC_AUTH_ROUTES = {
    f"{API}/auth/signup",
    f"{API}/auth/login",
    f"{API}/auth/logout",
}


@dataclass(frozen=True)
class World:
    app: FastAPI
    client_a: TestClient
    client_b: TestClient
    user_a: uuid.UUID
    user_b: uuid.UUID
    doc_b: uuid.UUID
    doc_public: uuid.UUID


def api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """(method, path) of every route under /api/v1.

    Read from the OpenAPI schema: it lists the routes of included routers with
    their real paths and methods.
    """
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if path.startswith(API):
            found.update((method.upper(), path) for method in operations)
    return found


def document_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {(m, p) for m, p in api_routes(app) if p.startswith(DOCUMENTS)}


@pytest.fixture
def world(
    app: FastAPI, client: TestClient, session_factory: sessionmaker[Session]
) -> World:
    # Two clients on the same app: each keeps its own cookie jar.
    client_a = client
    client_b = TestClient(app, base_url="https://testserver")
    user_a = login_as(client_a, "alice@example.org")
    user_b = login_as(client_b, "bob@example.org")
    doc_b = add_document(
        session_factory, user_b, title="bob", created_at=BASE, status="ok", sections=2
    )
    doc_public = add_document(
        session_factory, None, title="public", created_at=BASE, status="ok", sections=1
    )
    return World(app, client_a, client_b, user_a, user_b, doc_b, doc_public)


def test_every_document_route_hides_foreign_documents(
    world: World, session_factory: sessionmaker[Session]
) -> None:
    swept = 0
    for method, path in sorted(document_routes(world.app)):
        if "{document_id}" in path:
            url = path.replace("{document_id}", str(world.doc_b))
            response = world.client_a.request(method, url)
            assert response.status_code == 404, (method, url, response.text)
            swept += 1
        elif method == "GET":
            for scope in ("mine", "public", "all"):
                seen: list[str] = []
                cursor: str | None = None
                for _ in range(10):  # bounded: a page never loops forever
                    params = {"scope": scope, "limit": 1}
                    if cursor is not None:
                        params["cursor"] = cursor
                    response = world.client_a.get(path, params=params)
                    assert response.status_code == 200, response.text
                    page = response.json()
                    seen.extend(item["id"] for item in page["items"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                assert cursor is None, f"scope={scope} did not terminate"
                assert str(world.doc_b) not in seen, scope
            swept += 1
    # GET document, DELETE document, GET sections, GET list.
    assert swept == 4

    with session_factory() as session:
        assert session.get(Document, world.doc_b) is not None  # owner view: kept


def test_every_business_route_requires_login(world: World) -> None:
    anonymous = TestClient(world.app, base_url="https://testserver")
    checked = 0
    for method, path in sorted(api_routes(world.app)):
        if path in PUBLIC_AUTH_ROUTES:
            continue
        url = path.replace("{document_id}", str(world.doc_b))
        kwargs: dict[str, object] = {}
        if method == "POST" and path == DOCUMENTS:
            kwargs = {
                "params": {"filename": "paper.pdf"},
                "content": b"%PDF-1.7\n%%EOF\n",
                "headers": {"Content-Type": "application/pdf"},
            }
        response = anonymous.request(method, url, **kwargs)  # type: ignore[arg-type]
        assert response.status_code == 401, (method, url, response.text)
        checked += 1
    assert checked >= len(EXPECTED_DOCUMENT_ROUTES)


def test_guessing_ids_reveals_nothing(world: World) -> None:
    for suffix in ("", "/sections"):
        foreign = world.client_a.get(f"{DOCUMENTS}/{world.doc_b}{suffix}")
        random = world.client_a.get(f"{DOCUMENTS}/{uuid.uuid4()}{suffix}")
        assert foreign.status_code == random.status_code == 404
        assert foreign.content == random.content, suffix
    foreign_delete = world.client_a.delete(f"{DOCUMENTS}/{world.doc_b}")
    random_delete = world.client_a.delete(f"{DOCUMENTS}/{uuid.uuid4()}")
    assert foreign_delete.status_code == random_delete.status_code == 404
    assert foreign_delete.content == random_delete.content


def test_rls_holds_even_without_the_repository_filter(
    world: World, session_factory: sessionmaker[Session]
) -> None:
    with scoped_session(session_factory, world.user_a) as session:
        visible = {row[0] for row in session.execute(text("SELECT id FROM documents"))}
    assert world.doc_b not in visible
    assert world.doc_public in visible  # the public corpus stays readable


def test_new_routes_are_covered(world: World) -> None:
    assert document_routes(world.app) == EXPECTED_DOCUMENT_ROUTES
