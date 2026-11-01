import ast
import uuid
from pathlib import Path

import pytest
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from explorai_ai.scope import PUBLIC_OWNER, Scope, build_scope_filter

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


def conditions(flt: Filter) -> dict[str, object]:
    assert isinstance(flt.must, list)
    out: dict[str, object] = {}
    for cond in flt.must:
        assert isinstance(cond, FieldCondition)
        out[cond.key] = cond.match
    return out


def test_all_matches_user_and_public() -> None:
    match = conditions(build_scope_filter(USER, "all"))["owner_id"]
    assert isinstance(match, MatchAny)
    assert sorted(match.any) == sorted([str(USER), PUBLIC_OWNER])


def test_default_scope_is_all() -> None:
    assert build_scope_filter(USER) == build_scope_filter(USER, "all")


def test_mine_excludes_public() -> None:
    match = conditions(build_scope_filter(USER, "mine"))["owner_id"]
    assert match == MatchValue(value=str(USER))


def test_public_excludes_user() -> None:
    match = conditions(build_scope_filter(USER, "public"))["owner_id"]
    assert match == MatchValue(value=PUBLIC_OWNER)


def test_document_id_is_added() -> None:
    found = conditions(build_scope_filter(USER, "all", DOCUMENT))
    assert found["document_id"] == MatchValue(value=str(DOCUMENT))
    assert "owner_id" in found


@pytest.mark.parametrize("scope", ["all", "mine", "public"])
@pytest.mark.parametrize("document_id", [None, DOCUMENT])
def test_owner_condition_is_never_missing(
    scope: Scope, document_id: uuid.UUID | None
) -> None:
    assert "owner_id" in conditions(build_scope_filter(USER, scope, document_id))


# Only these files may build Qdrant filters. index.py (Task 4) is exempt for the
# point-id helper and collection plumbing; it does not exist yet.
ALLOWED_FILES = frozenset({"scope.py", "index.py"})
FILTER_CALLS = frozenset({"Filter", "FieldCondition", "MatchAny", "MatchValue"})
ROOT = Path(__file__).resolve().parents[3]


def called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_no_other_module_builds_a_filter() -> None:
    offenders: list[str] = []
    for package in ("ai/src", "backend/src", "pipelines/src"):
        for path in (ROOT / package).rglob("*.py"):
            if path.name in ALLOWED_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and called_name(node) in FILTER_CALLS:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
