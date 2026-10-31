"""Command line: run a flow once, or serve the nightly schedule.

python -m explorai_pipelines ingest-arxiv --query cat:cs.CL --max-results 10
python -m explorai_pipelines ingest-web https://example.org/page
python -m explorai_pipelines reextract --version pdf-2
python -m explorai_pipelines serve
"""

import argparse
from collections.abc import Sequence

from explorai_pipelines.flows.ingest_arxiv import ingest_arxiv_flow
from explorai_pipelines.flows.ingest_web import ingest_web_flow
from explorai_pipelines.flows.reextract import reextract_flow

NIGHTLY_QUERY = "cat:cs.CL"
NIGHTLY_MAX_RESULTS = 20
NIGHTLY_CRON = "0 2 * * *"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="explorai_pipelines")
    commands = parser.add_subparsers(dest="command", required=True)

    arxiv = commands.add_parser("ingest-arxiv", help="ingest recent arXiv papers")
    arxiv.add_argument("--query", required=True, help="arXiv search_query")
    arxiv.add_argument("--max-results", type=int, default=20)

    web = commands.add_parser("ingest-web", help="ingest web pages")
    web.add_argument("urls", nargs="+")

    again = commands.add_parser("reextract", help="re-extract stored artifacts")
    again.add_argument("--version", required=True, help="extractor version, e.g. pdf-2")

    commands.add_parser("serve", help="serve the nightly arXiv deployment")
    return parser


def run_arxiv(query: str, max_results: int) -> None:
    print(ingest_arxiv_flow(query, max_results))


def run_web(urls: list[str]) -> None:
    print(ingest_web_flow(urls))


def run_reextract(version: str) -> None:
    print(reextract_flow(version))


def serve() -> None:
    ingest_arxiv_flow.serve(
        name="nightly-arxiv",
        cron=NIGHTLY_CRON,
        parameters={"query": NIGHTLY_QUERY, "max_results": NIGHTLY_MAX_RESULTS},
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ingest-arxiv":
        run_arxiv(args.query, args.max_results)
    elif args.command == "ingest-web":
        run_web(args.urls)
    elif args.command == "reextract":
        run_reextract(args.version)
    else:
        serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
