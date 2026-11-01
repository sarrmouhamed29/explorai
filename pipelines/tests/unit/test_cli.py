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


def test_serve_registers_both_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[object] = []
    monkeypatch.setattr(
        cli, "prefect_serve", lambda *deployments: served.extend(deployments)
    )

    cli.serve()

    names = sorted(d.name for d in served)  # type: ignore[attr-defined]
    assert names == ["nightly-arxiv", "process-pending"]
