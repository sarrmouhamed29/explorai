from explorai_pipelines.sources.web import WebSource


def test_web_source_drops_fragment_and_dedups_urls() -> None:
    source = WebSource(
        [
            "https://example.org/a#intro",
            "https://example.org/a#conclusion",
            "https://example.org/b",
        ]
    )

    items = source.list_items()

    assert [i.external_id for i in items] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    assert [i.url for i in items] == [i.external_id for i in items]


def test_web_source_keeps_query_strings() -> None:
    items = WebSource(["https://example.org/p?id=3#top"]).list_items()
    assert items[0].external_id == "https://example.org/p?id=3"
