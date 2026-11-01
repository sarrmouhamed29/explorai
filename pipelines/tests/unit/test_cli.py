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
