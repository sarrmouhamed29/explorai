"""Helpers shared by the document integration tests.

Rows are inserted as the table owner (no RLS), standing in for the pipeline.
"""

import uuid
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import Document, DocumentVersion, RawArtifact, Section, Source

PASSWORD = "correct horse battery staple"


def login_as(client: TestClient, email: str) -> uuid.UUID:
    """Create the account (first time) and log in; return its user id."""
    response = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["id"])
    login_as_existing(client, email)
    return user_id


def login_as_existing(client: TestClient, email: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 204, response.text


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


def add_version(
    factory: sessionmaker[Session],
    document_id: uuid.UUID,
    status: str,
    created_at: datetime,
    *,
    texts: tuple[str, ...] = (),
    version_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Add one more version (own artifact) to an existing document."""
    with factory.begin() as session:
        document = session.get_one(Document, document_id)
        artifact = RawArtifact(
            source_id=document.source_id,
            external_id=document.external_id,
            url="u",
            sha256=uuid.uuid4().hex * 2,
            storage_key="k",
            owner_id=document.owner_id,
        )
        session.add(artifact)
        session.flush()
        version = DocumentVersion(
            document_id=document_id,
            artifact_id=artifact.id,
            extractor_version=f"v-{uuid.uuid4().hex[:8]}",
            status=status,
            created_at=created_at,
        )
        if version_id is not None:
            version.id = version_id
        session.add(version)
        session.flush()
        for ordinal, text in enumerate(texts):
            session.add(
                Section(version_id=version.id, ordinal=ordinal, heading=None, text=text)
            )
        return version.id
