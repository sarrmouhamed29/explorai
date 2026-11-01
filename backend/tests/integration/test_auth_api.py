"""Accounts, login and cookie sessions against real PostgreSQL and Redis."""

import hashlib
import logging

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_backend import deps
from explorai_db.models import User
from explorai_db.session import make_engine, make_session_factory

pytestmark = pytest.mark.integration

COOKIE = "explorai_session"
PASSWORD = "correct horse battery staple"
EMAIL = "alice@example.org"
SHORT = "tiny-pw-9x7"  # 11 characters, distinctive enough to search for


def signup(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> None:
    response = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text


def login(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 204, response.text
    return response.cookies[COOKIE]


def use_cookie(client: TestClient, token: str) -> None:
    """Present exactly this token: drop what the jar holds, then set it."""
    client.cookies.clear()
    client.cookies.set(COOKIE, token)


def test_signup_login_me_logout(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/signup", json={"email": "Alice@Example.org", "password": PASSWORD}
    )
    assert response.status_code == 201
    user = response.json()
    assert user == {"id": user["id"], "email": EMAIL}

    assert client.get("/api/v1/auth/me").status_code == 401
    login(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == user

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert COOKIE not in client.cookies
    assert client.get("/api/v1/auth/me").status_code == 401


def test_signup_duplicate_email_is_409(client: TestClient) -> None:
    signup(client)

    response = client.post(
        "/api/v1/auth/signup", json={"email": "ALICE@example.org", "password": PASSWORD}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "email already registered"}


@pytest.mark.parametrize("length", [0, 11, 129])
def test_short_or_long_password_is_422(client: TestClient, length: int) -> None:
    password = "p" * length

    for route in ("signup", "login"):
        response = client.post(
            f"/api/v1/auth/{route}", json={"email": EMAIL, "password": password}
        )
        assert response.status_code == 422
        if length:
            assert password not in response.text  # the input is not echoed


@pytest.mark.parametrize("length", [12, 128])
def test_password_length_bounds_are_accepted(client: TestClient, length: int) -> None:
    signup(client, password="p" * length)
    login(client, password="p" * length)


def test_invalid_email_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/signup", json={"email": "not-an-email", "password": PASSWORD}
    )

    assert response.status_code == 422


def test_wrong_password_and_unknown_email_get_the_same_answer(
    client: TestClient,
) -> None:
    signup(client)

    wrong = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD + "!"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "bob@example.org", "password": PASSWORD}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid credentials"}
    assert COOKIE not in wrong.cookies


def test_login_sets_httponly_samesite_cookie(client: TestClient) -> None:
    signup(client)

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )

    header = response.headers["set-cookie"]
    attributes = {part.strip().split("=")[0].lower() for part in header.split(";")}
    assert header.startswith(f"{COOKIE}=")
    assert {"httponly", "secure", "path", "max-age"} <= attributes
    assert "samesite=lax" in header.lower()
    assert "path=/;" in header.lower() + ";"
    assert "max-age=604800" in header.lower()


def test_cookie_is_not_secure_when_disabled(app: FastAPI, client: TestClient) -> None:
    from explorai_backend.config import Settings, get_settings

    settings: Settings = app.dependency_overrides[get_settings]()
    insecure = settings.model_copy(update={"session_cookie_secure": False})
    app.dependency_overrides[get_settings] = lambda: insecure
    signup(client)

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )

    assert "secure" not in response.headers["set-cookie"].lower()


def test_new_login_issues_a_new_token(client: TestClient) -> None:
    signup(client)
    first = login(client)

    second = login(client)

    assert first != second
    # The new login replaced the session the client presented (no fixation).
    use_cookie(client, first)
    stale = client.get("/api/v1/auth/me")
    use_cookie(client, second)
    fresh = client.get("/api/v1/auth/me")
    assert stale.status_code == 401
    assert fresh.status_code == 200


def test_garbage_cookie_is_401(client: TestClient) -> None:
    use_cookie(client, "garbage")

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "not authenticated"}


def test_logged_out_cookie_is_401(client: TestClient) -> None:
    signup(client)
    token = login(client)
    assert client.post("/api/v1/auth/logout").status_code == 204

    use_cookie(client, token)
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401


def test_logout_without_a_session_is_204(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_login_is_rate_limited_per_email(client: TestClient) -> None:
    signup(client)
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD + "!"}
        )
        assert response.status_code == 401

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "too many login attempts"}


def test_session_is_stored_hashed_with_the_idle_ttl(
    client: TestClient, redis_client: redis.Redis
) -> None:
    signup(client)
    token = login(client)

    key = f"session:{hashlib.sha256(token.encode()).hexdigest()}"
    assert redis_client.exists(key)
    assert 86_000 < redis_client.ttl(key) <= 86_400
    assert not any(token in str(k) for k in redis_client.keys("*"))


def test_redis_down_is_503(app: FastAPI, client: TestClient) -> None:
    signup(client)  # PostgreSQL only: works while Redis is still up
    token = login(client)
    down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    app.dependency_overrides[deps.get_redis] = lambda: down

    use_cookie(client, token)
    me = client.get("/api/v1/auth/me")
    login_attempt = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    logout = client.post("/api/v1/auth/logout")

    assert me.status_code == 503
    assert login_attempt.status_code == 503
    assert logout.status_code == 503
    assert me.json() == {"detail": "session store unavailable"}


def test_database_down_is_503(app: FastAPI, client: TestClient) -> None:
    engine = make_engine("postgresql://u:p@127.0.0.1:1/db?connect_timeout=1")
    app.dependency_overrides[deps.get_session_factory] = lambda: make_session_factory(
        engine
    )

    response = client.post(
        "/api/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    engine.dispose()


def test_no_secret_in_responses_or_logs(
    client: TestClient,
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []

    for response in (
        client.post("/api/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD}),
        client.post(
            "/api/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD}
        ),  # 409
        client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD + "!"}
        ),  # 401
        client.post("/api/v1/auth/signup", json={"email": EMAIL, "password": SHORT}),
        login_response := client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        ),
        client.get("/api/v1/auth/me"),
        client.post("/api/v1/auth/logout"),
        client.get("/api/v1/auth/me"),  # 401
    ):
        bodies.append(response.text)

    token = login_response.cookies[COOKIE]
    with session_factory() as session:
        stored_hash = session.scalars(
            select(User.password_hash).where(User.email == EMAIL)
        ).one()

    logs = "\n".join(
        f"{record.getMessage()} {record.args!r} {record.exc_text or ''}"
        for record in caplog.records
    )
    assert caplog.records  # the capture works: SQLAlchemy and httpx log at DEBUG
    for secret in (PASSWORD, SHORT, token, stored_hash):
        assert all(secret not in body for body in bodies), secret
        assert secret not in logs, secret
