"""Account routes and the ``current_user`` dependency.

Routes are sync ``def``: FastAPI runs them in its threadpool, where the blocking
Argon2, SQLAlchemy and redis-py calls do not stall the event loop.
"""

import logging
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, EmailStr, Field

from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.auth.sessions import SessionStore
from explorai_backend.config import Settings, get_settings
from explorai_backend.deps import get_redis, get_session_factory
from explorai_backend.repositories.users import EmailTaken, UsersRepository
from explorai_backend.services.auth import (
    AuthService,
    InvalidCredentials,
    PublicUser,
    TooManyAttempts,
)
from explorai_db.rls import SessionFactory

logger = logging.getLogger(__name__)

COOKIE_NAME = "explorai_session"

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Declares the cookie in the OpenAPI document; auto_error=False so that a
# missing cookie gets this module's 401 rather than FastAPI's default answer.
session_cookie = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


class Credentials(BaseModel):
    email: EmailStr
    # NIST SP 800-63B: length, not composition rules. The upper bound stops huge
    # inputs from being used to load the deliberately slow hash.
    password: str = Field(min_length=12, max_length=128)


def get_auth_service(
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> AuthService:
    window = settings.login_window_seconds
    return AuthService(
        UsersRepository(factory),
        SessionStore(
            client, settings.session_idle_seconds, settings.session_max_seconds
        ),
        FixedWindowLimiter(
            client, "login:email", settings.login_max_failures_per_email, window
        ),
        FixedWindowLimiter(
            client, "login:ip", settings.login_max_failures_per_ip, window
        ),
    )


Service = Annotated[AuthService, Depends(get_auth_service)]
Token = Annotated[str | None, Depends(session_cookie)]


def _session_store_down() -> HTTPException:
    # Fail closed: without Redis no session can be checked, so nobody gets in.
    # The exception text is not logged: it can carry the Redis URL.
    logger.warning("session store unavailable")
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "session store unavailable"
    )


def current_user(token: Token, service: Service) -> PublicUser:
    """The logged-in user, or ``401``; ``503`` when Redis is unreachable."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        user = service.user_for_token(token)
    except redis.RedisError:
        raise _session_store_down() from None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return user


CurrentUser = Annotated[PublicUser, Depends(current_user)]


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Email already registered"}},
)
def signup(credentials: Credentials, service: Service) -> PublicUser:
    try:
        return service.signup(credentials.email, credentials.password)
    except EmailTaken:
        # Reveals that the account exists: documented limitation without email
        # confirmation (spec section 6).
        raise HTTPException(
            status.HTTP_409_CONFLICT, "email already registered"
        ) from None


@router.post(
    "/login",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Invalid credentials"},
        429: {"description": "Too many login attempts"},
        503: {"description": "Session store unavailable"},
    },
)
def login(
    credentials: Credentials,
    request: Request,
    response: Response,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
    previous: Token,
) -> None:
    ip = request.client.host if request.client else "unknown"
    try:
        token = service.login(credentials.email, credentials.password, ip)
        if previous:
            # The session the client came with is replaced, not kept alongside.
            service.logout(previous)
    except InvalidCredentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid credentials"
        ) from None
    except TooManyAttempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts"
        ) from None
    except redis.RedisError:
        raise _session_store_down() from None
    response.set_cookie(
        COOKIE_NAME,
        token,
        # The browser may keep the cookie up to the absolute lifetime; the server
        # enforces the idle expiry on its side.
        max_age=settings.session_max_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={503: {"description": "Session store unavailable"}},
)
def logout(
    response: Response,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
    token: Token,
) -> None:
    """Delete the session server-side and clear the cookie. Idempotent."""
    if token:
        try:
            service.logout(token)
        except redis.RedisError:
            raise _session_store_down() from None
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get(
    "/me",
    responses={
        401: {"description": "Not authenticated"},
        503: {"description": "Session store unavailable"},
    },
)
def me(user: CurrentUser) -> PublicUser:
    return user
