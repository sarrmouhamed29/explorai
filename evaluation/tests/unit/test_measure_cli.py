from pathlib import Path

import pytest

from explorai_eval.measure import _git_sha, main


def test_fake_provider_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    assert main(["--provider", "fake"]) == 0


def test_fake_provider_writes_output_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    out = tmp_path / "report.md"
    main(["--provider", "fake", "--output", str(out)])
    assert out.exists()
    assert "fake" in out.read_text()


def test_missing_provider_arg_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_output_nonexistent_parent_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    out = tmp_path / "no_such_dir" / "report.md"
    assert main(["--provider", "fake", "--output", str(out)]) == 2


def test_git_sha_fallback_when_subprocess_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "subprocess.check_output",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no git")),
    )
    assert _git_sha() == "unknown"


def test_real_provider_exits_two_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    monkeypatch.setattr(
        "explorai_eval.measure._build_real",
        lambda settings: (_ for _ in ()).throw(
            RuntimeError("DATABASE_URL is required to read the chunk texts")
        ),
    )
    assert main(["--provider", "real"]) == 2
