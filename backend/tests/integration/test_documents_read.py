"""Reading documents: scopes, keyset pagination, status, sections, isolation."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import Document, DocumentVersion, RawArtifact, Section, Source

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def login_as(client: TestClient, email: str) -> uuid.UUID:
    """Create the account (first time) and log in; return its user id."""
    response = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["id"])
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 204, login.text
    return user_id


def add_document(
    factory: sessionmaker[Session],
    owner: uuid.UUID | None,
    *,
    title: str,
    created_at: datetime,
    status: str | None = None,
    sections: int = 0,
    kind: str = "arxiv",
) -> uuid.UUID:
    """Insert as the table owner (no RLS); status None means no version yet."""
    with factory.begin() as session:
        source = session.query(Source).filter_by(kind=kind, name="t").one_or_none()
        if source is None:
            source = Source(kind=kind, name="t", config={})
            session.add(source)
            session.flush()
        external_id = str(uuid.uuid4())
        document = Document(
            source_id=source.id,
            external_id=external_id,
            title=title,
            owner_id=owner,
            created_at=created_at,
        )
        session.add(document)
        session.flush()
        if status is not None:
            artifact = RawArtifact(
                source_id=source.id,
                external_id=external_id,
                url="u",
                sha256=uuid.uuid4().hex * 2,
                storage_key="k",
                owner_id=owner,
            )
            session.add(artifact)
            session.flush()
            version = DocumentVersion(
                document_id=document.id,
                artifact_id=artifact.id,
                extractor_version="v1",
                status=status,
            )
            session.add(version)
            session.flush()
            for ordinal in range(sections):
                session.add(
                    Section(
                        version_id=version.id,
                        ordinal=ordinal,
                        heading=f"h{ordinal}",
                        text=f"text {ordinal}",
                    )
                )
        return document.id


def ids(response_json: dict[str, object]) -> list[str]:
    items = response_json["items"]
    assert isinstance(items, list)
    return [item["id"] for item in items]


def test_list_scopes(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    alice = login_as(client, "alice@example.org")
    bob = login_as(client, "bob@example.org")
    mine = add_document(session_factory, alice, title="a", created_at=BASE)
    public = add_document(
        session_factory, None, title="p", created_at=BASE + timedelta(days=1)
    )
    theirs = add_document(
        session_factory, bob, title="b", created_at=BASE + timedelta(days=2)
    )
    login_as_existing(client, "alice@example.org")

    def listed(scope: str | None) -> dict[str, object]:
        params = {} if scope is None else {"scope": scope}
        response = client.get("/api/v1/documents", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    assert ids(listed("mine")) == [str(mine)]
    assert ids(listed("public")) == [str(public)]
    assert ids(listed("all")) == [str(public), str(mine)]
    assert ids(listed(None)) == [str(public), str(mine)]
    assert str(theirs) not in ids(listed("all"))
    owned = {item["id"]: item["owned"] for item in listed("all")["items"]}  # type: ignore[attr-defined]
    assert owned == {str(mine): True, str(public): False}
    assert client.get("/api/v1/documents", params={"scope": "bogus"}).status_code == 422


def login_as_existing(client: TestClient, email: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 204, response.text


def test_pagination_is_stable_when_documents_are_added(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    created = [
        add_document(
            session_factory, user, title=str(i), created_at=BASE + timedelta(days=i)
        )
        for i in range(5)
    ]
    newest_first = [str(i) for i in reversed(created)]

    first = client.get("/api/v1/documents", params={"limit": 2}).json()
    assert ids(first) == newest_first[:2]
    assert first["next_cursor"]

    # A newer document arrives between two page requests.
    add_document(
        session_factory, user, title="late", created_at=BASE + timedelta(days=30)
    )

    second = client.get(
        "/api/v1/documents", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert ids(second) == newest_first[2:4]
    third = client.get(
        "/api/v1/documents", params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()
    assert ids(third) == newest_first[4:]
    assert third["next_cursor"] is None


def test_equal_timestamps_are_ordered_by_id(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    created = sorted(
        (
            add_document(session_factory, user, title="t", created_at=BASE)
            for _ in range(3)
        ),
        reverse=True,
    )
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/documents", params=params).json()
        seen += ids(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(i) for i in created]


def test_limit_and_cursor_validation(client: TestClient) -> None:
    login_as(client, "alice@example.org")
    for limit in (0, 101):
        response = client.get("/api/v1/documents", params={"limit": limit})
        assert response.status_code == 422
    response = client.get("/api/v1/documents", params={"cursor": "garbage"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid cursor"}
    assert client.get("/api/v1/documents", params={"limit": 100}).status_code == 200


def test_status_processing_ready_failed(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    processing = add_document(session_factory, user, title="p", created_at=BASE)
    ready = add_document(
        session_factory, user, title="r", created_at=BASE, status="ok", sections=1
    )
    failed = add_document(
        session_factory, user, title="f", created_at=BASE, status="failed"
    )
    unextractable = add_document(
        session_factory, user, title="u", created_at=BASE, status="unextractable"
    )
    expected = {
        processing: "processing",
        ready: "ready",
        failed: "failed",
        unextractable: "failed",
    }
    for document_id, status in expected.items():
        response = client.get(f"/api/v1/documents/{document_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == status
        assert body["owned"] is True
        assert body["source_kind"] == "arxiv"
    listed = client.get("/api/v1/documents").json()["items"]
    assert {item["id"]: item["status"] for item in listed} == {
        str(k): v for k, v in expected.items()
    }


def test_sections_are_paginated_by_ordinal(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    document = add_document(
        session_factory, user, title="r", created_at=BASE, status="ok", sections=5
    )
    url = f"/api/v1/documents/{document}/sections"
    first = client.get(url, params={"limit": 2}).json()
    assert [s["ordinal"] for s in first["items"]] == [0, 1]
    assert first["items"][0] == {"ordinal": 0, "heading": "h0", "text": "text 0"}
    second = client.get(url, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [s["ordinal"] for s in second["items"]] == [2, 3]
    last = client.get(url, params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [s["ordinal"] for s in last["items"]] == [4]
    assert last["next_cursor"] is None
    assert client.get(url, params={"cursor": "garbage"}).status_code == 422


def test_sections_of_processing_document_is_409(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    processing = add_document(session_factory, user, title="p", created_at=BASE)
    failed = add_document(
        session_factory, user, title="f", created_at=BASE, status="failed"
    )
    for document in (processing, failed):
        response = client.get(f"/api/v1/documents/{document}/sections")
        assert response.status_code == 409


def test_public_documents_are_readable(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    public = add_document(
        session_factory, None, title="p", created_at=BASE, status="ok", sections=1
    )
    body = client.get(f"/api/v1/documents/{public}").json()
    assert body["owned"] is False
    assert client.get(f"/api/v1/documents/{public}/sections").status_code == 200


def test_unknown_id_is_404(client: TestClient) -> None:
    login_as(client, "alice@example.org")
    missing = uuid.uuid4()
    assert client.get(f"/api/v1/documents/{missing}").status_code == 404
    assert client.get(f"/api/v1/documents/{missing}/sections").status_code == 404


def test_someone_elses_document_is_404(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    bob = login_as(client, "bob@example.org")
    private = add_document(
        session_factory, bob, title="b", created_at=BASE, status="ok", sections=1
    )
    login_as_existing(client, "alice@example.org")
    unknown = client.get(f"/api/v1/documents/{uuid.uuid4()}")
    for suffix in ("", "/sections"):
        response = client.get(f"/api/v1/documents/{private}{suffix}")
        assert response.status_code == 404
        assert response.json() == unknown.json()


def test_malformed_id_is_422(client: TestClient) -> None:
    login_as(client, "alice@example.org")
    assert client.get("/api/v1/documents/not-a-uuid").status_code == 422
    assert client.get("/api/v1/documents/not-a-uuid/sections").status_code == 422


def test_routes_require_login(client: TestClient) -> None:
    some_id = uuid.uuid4()
    for path in (
        "/api/v1/documents",
        f"/api/v1/documents/{some_id}",
        f"/api/v1/documents/{some_id}/sections",
    ):
        assert client.get(path).status_code == 401
