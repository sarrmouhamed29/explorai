"""Uploading and deleting documents through the API, under Row-Level Security."""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from explorai_backend.api.documents import get_upload_limiter
from explorai_backend.config import Settings, get_settings
from explorai_db.models import Document, DocumentVersion, RawArtifact, Section, Source
from integration.helpers import (
    add_document,
    add_version,
    login_as,
    login_as_existing,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/documents"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def pdf(label: str) -> bytes:
    """A body that passes the magic-byte check; distinct labels, distinct hashes."""
    return b"%PDF-1.7\n% " + label.encode() + b"\n%%EOF\n"


def upload(
    client: TestClient, body: Any, filename: str | None = "paper.pdf", **kwargs: Any
) -> Any:
    params = {} if filename is None else {"filename": filename}
    return client.post(
        URL,
        params=params,
        content=body,
        headers={"Content-Type": "application/pdf", **kwargs.pop("headers", {})},
        **kwargs,
    )


def use_settings(app: FastAPI, settings: Settings, **changes: Any) -> None:
    changed = settings.model_copy(update=changes)
    app.dependency_overrides[get_settings] = lambda: changed


def blob_path(settings: Settings, data: bytes) -> Path:
    key = hashlib.sha256(data).hexdigest()
    return settings.blob_dir / key[:2] / key


def count(factory: sessionmaker[Session], model: Any, *where: Any) -> int:
    with factory.begin() as session:
        return (
            session.scalar(select(func.count()).select_from(model).where(*where)) or 0
        )


def test_upload_returns_202_and_processing(
    client: TestClient, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    data = pdf("one")

    response = upload(client, data, filename="../../My Paper.pdf")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "processing"
    assert body["owned"] is True
    assert body["source_kind"] == "upload"
    assert body["title"] == "My Paper"
    assert blob_path(settings, data).read_bytes() == data
    sha = hashlib.sha256(data).hexdigest()
    with session_factory.begin() as session:
        document = session.get_one(Document, uuid.UUID(body["id"]))
        assert document.owner_id == user
        assert document.external_id == sha
        source = session.get_one(Source, document.source_id)
        assert (source.kind, source.name) == ("upload", "upload")
        artifact = session.scalars(
            select(RawArtifact).where(RawArtifact.external_id == sha)
        ).one()
        assert artifact.owner_id == user
        assert (artifact.sha256, artifact.storage_key) == (sha, sha)
        assert artifact.source_id == source.id
    fetched = client.get(f"{URL}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "processing"


def test_upload_without_filename_is_untitled(client: TestClient) -> None:
    login_as(client, "alice@example.org")

    response = upload(client, pdf("anonymous"), filename=None)

    assert response.status_code == 202
    assert response.json()["title"] == "Untitled document"


def test_same_file_twice_returns_200_same_id(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    data = pdf("twice")

    first = upload(client, data)
    second = upload(client, data, filename="renamed.pdf")

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["title"] == "paper"
    assert count(session_factory, Document) == 1
    assert count(session_factory, RawArtifact) == 1


def test_same_file_from_two_users_gives_two_documents(client: TestClient) -> None:
    login_as(client, "alice@example.org")
    data = pdf("shared")
    alice = upload(client, data)
    login_as(client, "bob@example.org")
    bob = upload(client, data)

    assert (alice.status_code, bob.status_code) == (202, 202)
    assert alice.json()["id"] != bob.json()["id"]


def test_chunked_body_over_cap_is_413(
    app: FastAPI,
    client: TestClient,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "alice@example.org")
    use_settings(app, settings, upload_max_bytes=1_000)

    def chunks() -> Iterator[bytes]:
        yield b"%PDF-1.7\n"
        for _ in range(20):
            yield b"x" * 100

    # A generator body is sent chunked, without Content-Length: only the streamed
    # cap can stop it.
    response = upload(client, chunks())

    assert response.status_code == 413
    assert response.json() == {"detail": "upload too large"}
    assert count(session_factory, Document) == 0
    assert not any(settings.blob_dir.rglob("*"))


def test_declared_length_over_cap_is_413(
    app: FastAPI, client: TestClient, settings: Settings
) -> None:
    login_as(client, "alice@example.org")
    use_settings(app, settings, upload_max_bytes=10)

    response = upload(client, pdf("too long"))

    assert response.status_code == 413


def test_body_at_cap_is_accepted(
    app: FastAPI, client: TestClient, settings: Settings
) -> None:
    login_as(client, "alice@example.org")
    data = pdf("exact")
    use_settings(app, settings, upload_max_bytes=len(data))

    assert upload(client, data).status_code == 202


def test_empty_body_is_415(client: TestClient) -> None:
    login_as(client, "alice@example.org")

    response = upload(client, b"")

    assert response.status_code == 415
    assert response.json() == {"detail": "only PDF files are accepted"}


def test_non_pdf_is_415(client: TestClient, settings: Settings) -> None:
    login_as(client, "alice@example.org")

    # Content-Type and extension say PDF; the bytes decide.
    response = upload(client, b"<html><body>hi</body></html>", filename="x.pdf")

    assert response.status_code == 415
    assert not any(settings.blob_dir.rglob("*"))


def test_hourly_upload_limit_is_429(
    app: FastAPI, client: TestClient, settings: Settings
) -> None:
    login_as(client, "alice@example.org")
    use_settings(app, settings, upload_max_per_hour=2)

    # Rejected uploads do not consume the hourly budget.
    assert upload(client, b"not a pdf").status_code == 415
    assert upload(client, pdf("a")).status_code == 202
    # Re-sending a file already stored creates nothing and is not counted.
    assert upload(client, pdf("a")).status_code == 200
    assert upload(client, pdf("b")).status_code == 202

    response = upload(client, pdf("c"))
    assert response.status_code == 429
    assert response.json() == {"detail": "too many uploads"}

    # The limit is per user.
    login_as(client, "bob@example.org")
    assert upload(client, pdf("c")).status_code == 202


def test_document_quota_is_409(
    app: FastAPI,
    client: TestClient,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    user = login_as(client, "alice@example.org")
    use_settings(app, settings, max_documents_per_user=2)
    add_document(session_factory, user, title="existing", created_at=BASE)
    # Public documents do not count against a user's quota.
    add_document(session_factory, None, title="public", created_at=BASE)
    assert upload(client, pdf("a")).status_code == 202

    response = upload(client, pdf("b"))

    assert response.status_code == 409
    assert response.json() == {"detail": "document quota reached"}
    assert not blob_path(settings, pdf("b")).exists()
    # An upload already stored is answered even at the quota.
    assert upload(client, pdf("a")).status_code == 200
    assert count(session_factory, Document, Document.owner_id == user) == 2


def test_foreign_origin_upload_is_403(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")

    response = upload(client, pdf("csrf"), headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert count(session_factory, Document) == 0


def test_upload_requires_login(client: TestClient) -> None:
    assert upload(client, pdf("anon")).status_code == 401


class BrokenLimiter:
    """Stands in for the upload limiter when Redis fails at one step."""

    limit = 20

    def __init__(self, *, fail_hit: bool) -> None:
        self.fail_hit = fail_hit

    def hit(self, _key: str) -> int:
        if self.fail_hit:
            raise redis.ConnectionError("down")
        return 1

    def release(self, _key: str) -> None:
        raise redis.ConnectionError("down")


def test_rate_limiter_down_is_503(
    app: FastAPI, client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    app.dependency_overrides[get_upload_limiter] = lambda: BrokenLimiter(fail_hit=True)

    response = upload(client, pdf("no limiter"))

    # Fails closed: without the limiter nothing is accepted.
    assert response.status_code == 503
    assert response.json() == {"detail": "rate limiter unavailable"}
    assert count(session_factory, Document) == 0


def test_failed_release_keeps_the_answer(app: FastAPI, client: TestClient) -> None:
    login_as(client, "alice@example.org")
    app.dependency_overrides[get_upload_limiter] = lambda: BrokenLimiter(fail_hit=False)

    assert upload(client, b"not a pdf").status_code == 415


def test_delete_own_document(
    client: TestClient, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    data = pdf("to delete")
    document_id = uuid.UUID(upload(client, data).json()["id"])
    # The pipeline processes it: a version with sections.
    version_id = add_version(
        session_factory, document_id, "ok", BASE, texts=("one", "two")
    )

    response = client.delete(f"{URL}/{document_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert count(session_factory, Document, Document.id == document_id) == 0
    assert count(session_factory, DocumentVersion) == 0
    assert count(session_factory, Section, Section.version_id == version_id) == 0
    assert count(session_factory, RawArtifact) == 0
    # The blob is left for the pipeline's garbage collection.
    assert blob_path(settings, data).exists()
    assert client.get(f"{URL}/{document_id}").status_code == 404
    assert client.delete(f"{URL}/{document_id}").status_code == 404
    # Deleting frees the identity: the same bytes can be uploaded again.
    assert upload(client, data).status_code == 202


def test_delete_public_is_403(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    public = add_document(
        session_factory, None, title="p", created_at=BASE, status="ok", sections=1
    )

    response = client.delete(f"{URL}/{public}")

    assert response.status_code == 403
    assert count(session_factory, Document, Document.id == public) == 1


def test_delete_someone_elses_document_is_404(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "alice@example.org")
    bob = login_as(client, "bob@example.org")
    private = add_document(session_factory, bob, title="b", created_at=BASE)
    login_as_existing(client, "alice@example.org")
    unknown = client.delete(f"{URL}/{uuid.uuid4()}")

    response = client.delete(f"{URL}/{private}")

    assert response.status_code == 404
    assert response.json() == unknown.json()
    assert count(session_factory, Document, Document.id == private) == 1


def test_delete_canonical_keeps_duplicate_with_null_pointer(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    user = login_as(client, "alice@example.org")
    canonical_doc = add_document(session_factory, user, title="c", created_at=BASE)
    canonical = add_version(session_factory, canonical_doc, "ok", BASE, texts=("t",))
    duplicate_doc = add_document(session_factory, user, title="d", created_at=BASE)
    duplicate = add_version(session_factory, duplicate_doc, "ok", BASE, texts=("t",))
    with session_factory.begin() as session:
        session.get_one(DocumentVersion, duplicate).duplicate_of = canonical

    response = client.delete(f"{URL}/{canonical_doc}")

    assert response.status_code == 204
    with session_factory.begin() as session:
        kept = session.get_one(DocumentVersion, duplicate)
        assert kept.duplicate_of is None
        assert session.get(DocumentVersion, canonical) is None
    assert client.get(f"{URL}/{duplicate_doc}/sections").status_code == 200


def test_delete_requires_login(client: TestClient) -> None:
    assert client.delete(f"{URL}/{uuid.uuid4()}").status_code == 401
