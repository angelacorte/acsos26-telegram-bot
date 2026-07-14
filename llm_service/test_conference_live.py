"""Tests for bounded live ACSOS website retrieval."""

from __future__ import annotations

import time
from email.message import Message
from pathlib import Path

import pytest

from llm_service.conference_live import (
    ConferenceLiveRetriever,
    ConferencePageCache,
    ConferencePageFetcher,
    ConferenceSiteSearch,
    LiveSearchConfig,
    PageRecord,
    discover_catalog,
    extract_page_record,
    normalize_candidate_url,
    rank_live_chunks,
)


def test_normalizes_allowed_urls_and_blocks_unsafe_targets(tmp_path: Path) -> None:
    """URL normalization should enforce scheme, host, path, and query restrictions."""
    config = make_config(tmp_path)

    assert normalize_candidate_url("/dates#top", config.base_url, config.allowed_hosts) == "https://2026.acsos.org/dates"
    assert normalize_candidate_url("https://2026.acsos.org/attending/Registration", config.base_url, config.allowed_hosts)
    assert normalize_candidate_url("https://example.org/dates", config.base_url, config.allowed_hosts) is None
    assert normalize_candidate_url("file:///etc/passwd", config.base_url, config.allowed_hosts) is None
    assert normalize_candidate_url("http://127.0.0.1/admin", config.base_url, ("127.0.0.1",)) is None
    assert normalize_candidate_url("https://2026.acsos.org/signin", config.base_url, config.allowed_hosts) is None
    assert normalize_candidate_url("https://2026.acsos.org/dates?track=main", config.base_url, config.allowed_hosts) is None
    assert normalize_candidate_url("https://2026.acsos.org/search//all", config.base_url, config.allowed_hosts) is None
    assert normalize_candidate_url("https://conf.researchr.org/home/acsos-2021", config.base_url, config.allowed_hosts) is None


def test_extract_page_record_removes_navigation_and_footer() -> None:
    """Main text extraction should drop repeated conference boilerplate."""
    html = """
    <html><head><title>Important Dates - ACSOS 2026</title></head>
    <body>
      <nav>Toggle navigation Sign in Search</nav>
      <h1>Important Dates</h1>
      <table><tr><td>Mon 20 Jul 2026</td><td>Main Track Camera ready deadline</td></tr></table>
      <footer>x Tue 14 Jul 12:49 using conf.researchr.org</footer>
    </body></html>
    """

    page = extract_page_record("https://2026.acsos.org/dates", "https://2026.acsos.org/dates", html, {}, "dynamic")

    assert page.title == "Important Dates"
    assert "Main Track Camera ready deadline" in page.text
    assert "Toggle navigation" not in page.text
    assert "conf.researchr.org" not in page.text


def test_site_search_ranks_specific_dynamic_pages(tmp_path: Path) -> None:
    """URL ranking should prefer specific pages over the homepage."""
    config = make_config(tmp_path)
    search = ConferenceSiteSearch(config)

    urls = [candidate.url for candidate in search.search("latest registration fees")]

    assert urls[0] == "https://2026.acsos.org/attending/Registration"
    assert len(urls) <= config.max_search_results


def test_fetcher_uses_cache_etag_and_304(tmp_path: Path) -> None:
    """Stale cached pages should be revalidated with conditional headers."""
    config = make_config(tmp_path, cache_ttl_dynamic_seconds=0)
    cache = ConferencePageCache(config.cache_path)
    fetcher = StubFetcher(
        config,
        cache,
        [
            (
                200,
                "https://2026.acsos.org/dates",
                {"content-type": "text/html", "etag": '"v1"', "last-modified": "Tue, 14 Jul 2026 10:00:00 GMT"},
                b"<html><head><title>Important Dates</title></head><body><h1>Important Dates</h1><p>Camera ready deadline</p></body></html>",
            ),
            (304, "https://2026.acsos.org/dates", {}, b""),
        ],
    )

    first = fetcher.fetch_sync("https://2026.acsos.org/dates", "dynamic")
    second = fetcher.fetch_sync("https://2026.acsos.org/dates", "dynamic")

    assert first.content_hash == second.content_hash
    assert fetcher.seen_headers[-1]["If-None-Match"] == '"v1"'
    assert fetcher.seen_headers[-1]["If-Modified-Since"] == "Tue, 14 Jul 2026 10:00:00 GMT"
    assert second.fetched_at >= first.fetched_at


def test_fetcher_retries_temporary_http_failures(tmp_path: Path) -> None:
    """Temporary HTTP errors should get one short retry."""
    config = make_config(tmp_path)
    fetcher = StubFetcher(
        config,
        ConferencePageCache(config.cache_path),
        [
            (500, "https://2026.acsos.org/news", {"content-type": "text/html"}, b"temporary"),
            (
                200,
                "https://2026.acsos.org/news",
                {"content-type": "text/html"},
                b"<html><head><title>News</title></head><body><h1>News</h1><p>Newsletter posted</p></body></html>",
            ),
        ],
    )

    page = fetcher.fetch_sync("https://2026.acsos.org/news", "dynamic")

    assert page.title == "News"
    assert "Newsletter posted" in page.text
    assert len(fetcher.seen_headers) == 2


def test_fetcher_blocks_redirect_to_external_host(tmp_path: Path) -> None:
    """Redirects must remain inside the configured host allowlist."""
    config = make_config(tmp_path)
    fetcher = StubFetcher(
        config,
        ConferencePageCache(config.cache_path),
        [(302, "https://2026.acsos.org/dates", {"location": "https://example.org/steal"}, b"")],
    )

    with pytest.raises(ValueError):
        fetcher.fetch_sync("https://2026.acsos.org/dates", "dynamic")


def test_rank_live_chunks_deduplicates_and_scores_relevance() -> None:
    """Chunk ranking should remove duplicate text and keep relevant snippets."""
    now = time.time()
    pages = [
        PageRecord(
            requested_url="https://2026.acsos.org/dates",
            final_url="https://2026.acsos.org/dates",
            title="Important Dates",
            text="Camera ready deadline is July 20.\n\nCamera ready deadline is July 20.\n\nUnrelated venue paragraph.",
            fetched_at=now,
            category="dynamic",
        ),
    ]

    chunks = rank_live_chunks("camera ready deadline", pages)

    assert chunks
    assert chunks[0].source == "https://2026.acsos.org/dates"
    assert sum("Camera ready deadline" in chunk.text for chunk in chunks) == 1


@pytest.mark.anyio
async def test_live_retriever_limits_pages(tmp_path: Path) -> None:
    """The retriever should fetch no more than the configured page limit."""
    config = make_config(tmp_path, max_pages_per_query=2, overall_timeout_seconds=1)
    data_path = tmp_path / "conference.json"
    data_path.write_text("{}", encoding="utf-8")
    fetcher = CountingFetcher(config, ConferencePageCache(config.cache_path))
    retriever = ConferenceLiveRetriever(
        config,
        ConferenceSiteSearch(config),
        fetcher,
        data_path,
    )

    result = await retriever.retrieve("latest news", [])

    assert result.used_live
    assert fetcher.calls == 2


@pytest.mark.anyio
async def test_live_retriever_times_out_and_falls_back(tmp_path: Path) -> None:
    """A slow live lookup should return a fallback result instead of blocking."""
    config = make_config(tmp_path, overall_timeout_seconds=0.01)
    data_path = tmp_path / "conference.json"
    data_path.write_text("{}", encoding="utf-8")
    retriever = ConferenceLiveRetriever(
        config,
        ConferenceSiteSearch(config),
        SlowFetcher(config, ConferencePageCache(config.cache_path)),
        data_path,
    )

    result = await retriever.retrieve("latest news", [])

    assert result.used_live
    assert result.chunks == []
    assert result.error


def test_live_policy_uses_live_for_missing_local_context(tmp_path: Path) -> None:
    """Local misses should activate live retrieval when live search is enabled."""
    config = make_config(tmp_path)
    data_path = tmp_path / "conference.json"
    data_path.write_text("{}", encoding="utf-8")
    retriever = ConferenceLiveRetriever(
        config,
        ConferenceSiteSearch(config),
        AlwaysFailFetcher(config, ConferencePageCache(config.cache_path)),
        data_path,
    )

    assert retriever.should_use_live("what is the latest newsletter?", [])


def test_live_policy_uses_live_for_dynamic_questions_with_local_context(tmp_path: Path) -> None:
    """Dynamic questions should use live lookup even when local data has a match."""
    config = make_config(tmp_path)
    data_path = tmp_path / "conference.json"
    data_path.write_text("{}", encoding="utf-8")
    retriever = ConferenceLiveRetriever(
        config,
        ConferenceSiteSearch(config),
        AlwaysFailFetcher(config, ConferencePageCache(config.cache_path)),
        data_path,
    )

    assert retriever.should_use_live("what is the current registration fee?", [object()])


def test_catalog_discovery_adds_valid_internal_links(tmp_path: Path) -> None:
    """Catalog refresh should include normalized links discovered from navigation."""
    config = make_config(tmp_path)
    fetcher = CatalogFetcher()

    candidates = discover_catalog(config, fetcher)  # type: ignore[arg-type]
    urls = {candidate.url for candidate in candidates}

    assert "https://2026.acsos.org/attending/accommodation" in urls
    assert "https://2026.acsos.org/search//all" not in urls
    assert "https://example.org/outside" not in urls


class StubFetcher(ConferencePageFetcher):
    """Fetcher with canned HTTP responses."""

    def __init__(
        self,
        config: LiveSearchConfig,
        cache: ConferencePageCache,
        responses: list[tuple[int, str, dict[str, str], bytes]],
    ) -> None:
        super().__init__(config, cache)
        self.responses = responses
        self.seen_headers: list[dict[str, str]] = []

    def _request_once(self, url: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str], bytes]:
        self.seen_headers.append(headers)
        status, final_url, response_headers, body = self.responses.pop(0)
        return status, final_url, {key.lower(): value for key, value in response_headers.items()}, body


class AlwaysFailFetcher(ConferencePageFetcher):
    """Fetcher that simulates unavailable pages without raising from gather."""

    async def fetch(self, url: str, category: str = "standard") -> PageRecord:
        """Fail like a transient HTTP problem."""
        raise RuntimeError("temporary HTTP failure")


class SlowFetcher(ConferencePageFetcher):
    """Fetcher that exceeds the overall live retrieval timeout."""

    async def fetch(self, url: str, category: str = "standard") -> PageRecord:
        """Sleep longer than the configured test timeout."""
        import asyncio

        await asyncio.sleep(1)
        raise RuntimeError("unreachable")


class CountingFetcher(ConferencePageFetcher):
    """Fetcher that counts bounded live page fetches."""

    calls = 0

    async def fetch(self, url: str, category: str = "standard") -> PageRecord:
        """Return a small page while counting calls."""
        self.calls += 1
        return PageRecord(
            requested_url=url,
            final_url=url,
            title="News",
            text="Latest news update.",
            fetched_at=time.time(),
            category=category,
        )


class CatalogFetcher:
    """Small catalog fetcher stub with one page containing internal and rejected links."""

    def fetch_sync(self, url: str, category: str = "standard") -> PageRecord:
        """Return a page with representative navigation links."""
        return PageRecord(
            requested_url=url,
            final_url=url,
            title="ACSOS 2026",
            text="Conference overview",
            fetched_at=time.time(),
            category=category,
            links=(
                ("Accommodation", "https://2026.acsos.org/attending/accommodation"),
                ("Search", "https://2026.acsos.org/search//all"),
                ("Outside", "https://example.org/outside"),
            ),
        )


def make_config(tmp_path: Path, **overrides: object) -> LiveSearchConfig:
    """Return an isolated live-search config for tests."""
    values = {
        "cache_path": tmp_path / "cache.json",
        "catalog_path": tmp_path / "catalog.json",
        "max_search_results": 5,
        "max_pages_per_query": 3,
    }
    values.update(overrides)
    return LiveSearchConfig(**values)
