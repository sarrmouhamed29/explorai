"""Structural rules that keep the row-level-security boundary intact."""

import ast
from pathlib import Path

from explorai_backend.main import create_app

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED_FILE = SRC / "explorai_backend" / "deps.py"
# Health probes open raw psycopg/redis connections (no SQLAlchemy, no user data,
# no RLS involved). Exempted by exact path, like deps.py.
EXEMPT_FILES = frozenset({ALLOWED_FILE, SRC / "explorai_backend" / "db.py"})
FORBIDDEN_CALLS = frozenset(
    {
        "sessionmaker",
        "Session",
        "AsyncSession",
        "make_session_factory",
        "make_engine",
        "create_engine",
        "get_session_factory",
    }
)
FORBIDDEN_METHODS = frozenset({"begin", "begin_nested", "connect"})


def find_violations(source: str, filename: str = "<src>") -> list[str]:
    """Return ``file:line: reason`` for each way of opening a session or transaction.

    Working on the AST ignores comments and docstrings and sees through aliased
    imports, which a substring search cannot.
    """
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                aliases[item.asname or item.name] = item.name
                if module == "explorai_db.session" or (
                    module == "explorai_db" and item.name == "session"
                ):
                    violations.append(
                        f"{filename}:{node.lineno}: import from explorai_db.session"
                    )
                elif module == "sqlalchemy.orm" and item.name == "sessionmaker":
                    violations.append(
                        f"{filename}:{node.lineno}: import of sessionmaker"
                    )
        elif isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "explorai_db.session":
                    violations.append(
                        f"{filename}:{node.lineno}: import of explorai_db.session"
                    )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = aliases.get(func.id, func.id)
            if name in FORBIDDEN_CALLS:
                violations.append(f"{filename}:{node.lineno}: call to {name}()")
        elif isinstance(func, ast.Attribute):
            if func.attr in FORBIDDEN_CALLS or func.attr in FORBIDDEN_METHODS:
                violations.append(f"{filename}:{node.lineno}: call to .{func.attr}()")
    return sorted(violations)


def test_no_backend_module_opens_sessions_outside_scoped_session() -> None:
    violations = [
        violation
        for path in SRC.rglob("*.py")
        if path not in EXEMPT_FILES
        for violation in find_violations(
            path.read_text(encoding="utf-8"), str(path.relative_to(SRC))
        )
    ]

    assert violations == []


def test_checker_flags_each_forbidden_form() -> None:
    forbidden = [
        "from sqlalchemy.orm import sessionmaker",
        "from sqlalchemy.orm import Session\nSession(engine)",
        "from sqlalchemy.orm import Session\nSession (engine)",
        "from sqlalchemy.ext.asyncio import AsyncSession\nAsyncSession(engine)",
        "from explorai_db.session import make_session_factory",
        "from explorai_db import session",
        "import explorai_db.session",
        "make_session_factory(engine)",
        "make_engine(url)",
        "create_engine(url)",
        "get_session_factory()()",
        "from sqlalchemy.orm import sessionmaker as sm\nsm(bind=e)",
        "from x import make_engine as me\nme(url)",
        "import sqlalchemy.orm as o\no.sessionmaker(bind=e)",
        "conn.begin()",
        "conn.begin_nested()",
        "engine.connect()",
    ]

    for source in forbidden:
        assert find_violations(source), source


def test_checker_reports_file_line_and_call() -> None:
    violations = find_violations("x = 1\nmake_engine(url)\n", "api/x.py")

    assert violations == ["api/x.py:2: call to make_engine()"]


def test_checker_ignores_comments_docstrings_and_scoped_session() -> None:
    source = '''
"""Never call Session(engine) or sessionmaker( here."""
# Session(engine) .begin( would be wrong
from explorai_db.rls import scoped_session

def handler(factory):
    """Uses scoped_session, not Session(engine)."""
    with scoped_session(factory, 1) as session:
        return session
'''

    assert find_violations(source) == []


def test_only_the_exact_deps_file_is_exempt() -> None:
    assert ALLOWED_FILE.is_file()
    assert find_violations("make_engine(url)", "explorai_backend/x/deps.py")


def test_business_routes_are_versioned() -> None:
    paths = create_app().openapi()["paths"]
    business = [path for path in paths if not path.startswith("/health")]

    assert all(path.startswith("/api/v1") for path in business)
