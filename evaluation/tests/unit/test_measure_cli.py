from pathlib import Path

import pytest

from explorai_eval.measure import main


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
