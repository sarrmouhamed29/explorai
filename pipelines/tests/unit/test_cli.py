from typing import Any

import pytest

from explorai_pipelines import __main__ as cli


def test_parser_reads_ingest_arxiv_arguments() -> None:
    args = cli.build_parser().parse_args(
        ["ingest-arxiv", "--query", "cat:cs.CL", "--max-results", "7"]
    )
    assert (args.command, args.query, args.max_results) == (
        "ingest-arxiv",
        "cat:cs.CL",
        7,
    )


def test_parser_reads_web_urls_and_reextract_version() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["ingest-web", "https://a.org", "https://b.org"]).urls == [
        "https://a.org",
        "https://b.org",
    ]
    assert parser.parse_args(["reextract", "--version", "pdf-2"]).version == "pdf-2"


def test_command_is_required() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_main_dispatches_to_the_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        cli, "run_arxiv", lambda query, max_results: seen.append((query, max_results))
    )

    assert cli.main(["ingest-arxiv", "--query", "cat:cs.LG", "--max-results", "3"]) == 0
    assert seen == [("cat:cs.LG", 3)]


def test_parser_knows_process_pending() -> None:
    assert (
        cli.build_parser().parse_args(["process-pending"]).command == "process-pending"
    )


def test_main_dispatches_process_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli, "run_process_pending", lambda: seen.append("ran"))

    assert cli.main(["process-pending"]) == 0
    assert seen == ["ran"]


def served_deployments(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    served: list[Any] = []
    monkeypatch.setattr(
        cli, "prefect_serve", lambda *deployments: served.extend(deployments)
    )
    cli.serve()
    return served


def test_serve_without_rag_registers_the_ingestion_deployments_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)

    served = served_deployments(monkeypatch)

    assert sorted(d.name for d in served) == ["nightly-arxiv", "process-pending"]
    assert "RAG_PROVIDER is not set" in caplog.text


def test_serve_with_rag_registers_four_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")

    served = served_deployments(monkeypatch)

    by_name = {d.name: d for d in served}
    assert sorted(by_name) == [
        "index-pending",
        "nightly-arxiv",
        "process-pending",
        "prune-index",
    ]
    assert by_name["index-pending"].schedules[0].schedule.interval.total_seconds() == 30
    assert by_name["prune-index"].schedules[0].schedule.interval.total_seconds() == 3600


@pytest.mark.parametrize("command", ["index-pending", "prune-index"])
def test_parser_knows_the_index_commands(command: str) -> None:
    assert cli.build_parser().parse_args([command]).command == command


def test_main_dispatches_the_index_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    seen: list[str] = []
    monkeypatch.setattr(cli, "run_index_pending", lambda: seen.append("index"))
    monkeypatch.setattr(cli, "run_prune_index", lambda: seen.append("prune"))

    assert cli.main(["index-pending"]) == 0
    assert cli.main(["prune-index"]) == 0
    assert seen == ["index", "prune"]


@pytest.mark.parametrize("command", ["index-pending", "prune-index"])
def test_index_commands_without_rag_name_the_variable(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "run_index_pending", lambda: pytest.fail("ran"))
    monkeypatch.setattr(cli, "run_prune_index", lambda: pytest.fail("ran"))

    assert cli.main([command]) == 2
    assert "RAG_PROVIDER" in capsys.readouterr().err
