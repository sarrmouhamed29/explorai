"""``ScopedSessions`` without a database: a fake factory records the statements."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from explorai_db.rls import APP_ROLE, ScopedSessions, SessionFactory


class FakeSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.statements.append((str(statement), params))


class FakeFactory:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.calls = 0

    @contextmanager
    def begin(self) -> Iterator[FakeSession]:
        self.calls += 1
        yield self.session


def test_scoped_switches_role_and_identity_before_yielding() -> None:
    factory = FakeFactory()
    sessions = ScopedSessions(cast(SessionFactory, factory))
    user_id = uuid.uuid4()

    with sessions.scoped(user_id) as session:
        assert session is factory.session  # type: ignore[comparison-overlap]

    assert factory.calls == 1
    assert factory.session.statements == [
        (f"SET LOCAL ROLE {APP_ROLE}", None),
        ("SELECT set_config('app.user_id', :uid, true)", {"uid": str(user_id)}),
    ]


def test_scoped_without_identity_sets_an_empty_identity() -> None:
    factory = FakeFactory()

    with ScopedSessions(cast(SessionFactory, factory)).scoped(None):
        pass

    assert factory.session.statements[1][1] == {"uid": ""}


def test_the_factory_is_not_reachable_through_a_public_name() -> None:
    sessions = ScopedSessions(cast(SessionFactory, FakeFactory()))

    public = [name for name in dir(sessions) if not name.startswith("_")]

    assert public == ["scoped"]
    # __slots__: no instance __dict__ to hang the factory on, or to read it from.
    assert not hasattr(sessions, "__dict__")
