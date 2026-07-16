"""Live ACSOS 2026 website retrieval with bounded HTTP access and caching."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

LOGGER = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://2026.acsos.org"
DEFAULT_CACHE_PATH = Path(os.getenv("TMPDIR", "/tmp")) / "acsos26-live-page-cache.json"
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "src/main/resources/acsos26/site_catalog.json"
MAX_RESPONSE_BYTES = 1_000_000
MAX_CHUNK_CHARS = 1100
CHUNK_OVERLAP_CHARS = 180
LIVE_CONTEXT_BUDGET_CHARS = 6000
STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "the",
    "this",
    "to",
    "what",
    "which",
    "who",
    "with",
}
DYNAMIC_TERMS = {
    "accommodation",
    "agenda",
    "camera",
    "current",
    "currently",
    "date",
    "dates",
    "deadline",
    "deadlines",
    "dinner",
    "event",
    "hotel",
    "keynote",
    "latest",
    "news",
    "program",
    "recent",
    "registration",
    "reception",
    "schedule",
    "session",
    "sessions",
    "social",
    "speaker",
    "speakers",
    "today",
    "transport",
    "travel",
    "updated",
    "venue",
    "when",
    "workshop",
}
DISALLOWED_PATH_PARTS = {"/signin", "/signup", "/pagenotfound"}
DISALLOWED_PATH_PREFIXES = ("/getFile", "/javascript", "/search", "/stylesheets", "/support")
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
ALLOWED_PATH_PREFIXES = (
    "/",
    "/attending",
    "/committee",
    "/contact",
    "/dates",
    "/info",
    "/news",
    "/people-index",
    "/profile",
    "/series",
    "/track",
    "/venue",
)
DEFAULT_CATALOG_SEEDS = (
    ("https://2026.acsos.org/", "ACSOS 2026", "home", "conference overview latest news important dates"),
    ("https://2026.acsos.org/dates", "Important Dates", "dynamic", "deadlines notifications camera ready dates"),
    ("https://2026.acsos.org/news", "News Items", "dynamic", "latest news newsletter updates"),
    (
        "https://2026.acsos.org/info/program-at-a-glance",
        "Program at a Glance",
        "dynamic",
        "tentative timetable schedule main track sessions monday tuesday wednesday thursday friday",
    ),
    ("https://2026.acsos.org/attending/Registration", "Registration", "dynamic", "registration fees cvent author registration"),
    ("https://2026.acsos.org/attending/main-social-event", "Main Social Event", "dynamic", "social dinner event"),
    ("https://2026.acsos.org/attending/social-events", "Additional Social Events", "dynamic", "social events dinner"),
    ("https://2026.acsos.org/attending/travel-information", "Travel Information", "standard", "travel transport cesena"),
    ("https://2026.acsos.org/attending/accommodation", "Accommodation", "standard", "hotel accommodation"),
    ("https://2026.acsos.org/attending/visit-cesena", "Visit Cesena", "standard", "hotel accommodation"),
    ("https://2026.acsos.org/venue/acsos-2026-venue", "Venue", "standard", "venue room university bologna cesena campus"),
    ("https://2026.acsos.org/info/keynotes", "Keynotes", "dynamic", "keynote speakers talks"),
    ("https://2026.acsos.org/info/seminar-series", "Seminar series", "dynamic", "seminar program speakers"),
    ("https://2026.acsos.org/committee/acsos-2026-organizing-committee", "Organizing Committee", "standard", "chairs organizers committee"),
    ("https://2026.acsos.org/track/acsos-2026-papers", "Main Track", "dynamic", "accepted papers call camera ready"),
    ("https://2026.acsos.org/track/acsos-2026-workshops", "Workshops", "dynamic", "workshops call papers"),
    ("https://2026.acsos.org/track/acsos-2026-tutorials", "Tutorials", "dynamic", "tutorials call dates"),
    ("https://2026.acsos.org/track/acsos-2026-artifacts", "Artifacts", "dynamic", "artifact evaluation accepted"),
    ("https://2026.acsos.org/track/acsos-2026-doctoral-symposium", "Doctoral Symposium", "dynamic", "doctoral symposium accepted"),
    ("https://2026.acsos.org/track/acsos-2026-posters-and-demos", "Posters and Demos", "dynamic", "poster demo accepted"),
)


@dataclass(frozen=True)
class LiveSearchConfig:
    """Configuration for bounded ACSOS live retrieval."""

    base_url: str = DEFAULT_BASE_URL
    enabled: bool = True
    max_search_results: int = 5
    max_pages_per_query: int = 3
    max_live_tool_calls: int = 2
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 7.0
    overall_timeout_seconds: float = 10.0
    cache_ttl_dynamic_seconds: float = 900.0
    cache_ttl_standard_seconds: float = 21600.0
    cache_ttl_static_seconds: float = 86400.0
    user_agent: str = "acsos26-telegram-bot/1.0 (+https://2026.acsos.org)"
    cache_path: Path = DEFAULT_CACHE_PATH
    catalog_path: Path = DEFAULT_CATALOG_PATH
    allowed_hosts: tuple[str, ...] = ("2026.acsos.org", "conf.researchr.org")

    @classmethod
    def from_environment(cls) -> "LiveSearchConfig":
        """Build live-search settings from environment variables."""
        base_url = os.getenv("ACSOS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        return cls(
            base_url=base_url,
            enabled=parse_bool_env("ACSOS_LIVE_SEARCH_ENABLED", True),
            max_search_results=parse_int_env("ACSOS_MAX_SEARCH_RESULTS", 5),
            max_pages_per_query=parse_int_env("ACSOS_MAX_PAGES_PER_QUERY", 3),
            max_live_tool_calls=parse_int_env("ACSOS_MAX_LIVE_TOOL_CALLS", 2),
            connect_timeout_seconds=parse_float_env("ACSOS_CONNECT_TIMEOUT_SECONDS", 3.0),
            read_timeout_seconds=parse_float_env("ACSOS_READ_TIMEOUT_SECONDS", 7.0),
            overall_timeout_seconds=parse_float_env("ACSOS_OVERALL_TIMEOUT_SECONDS", 10.0),
            cache_ttl_dynamic_seconds=parse_float_env("ACSOS_CACHE_TTL_DYNAMIC_SECONDS", 900.0),
            cache_ttl_standard_seconds=parse_float_env("ACSOS_CACHE_TTL_STANDARD_SECONDS", 21600.0),
            cache_ttl_static_seconds=parse_float_env("ACSOS_CACHE_TTL_STATIC_SECONDS", 86400.0),
            user_agent=os.getenv("ACSOS_USER_AGENT", cls.user_agent),
            cache_path=Path(os.getenv("ACSOS_PAGE_CACHE", str(DEFAULT_CACHE_PATH))),
            catalog_path=Path(os.getenv("ACSOS_CATALOG_PATH", str(DEFAULT_CATALOG_PATH))),
        )


@dataclass(frozen=True)
class UrlCandidate:
    """A known conference URL that can be ranked for live retrieval."""

    url: str
    title: str
    category: str = "standard"
    summary: str = ""
    discovered_at: float = 0.0
    updated_at: float = 0.0
    content_hash: str = ""


@dataclass(frozen=True)
class PageRecord:
    """Fetched conference page content stored in the live cache."""

    requested_url: str
    final_url: str
    title: str
    text: str
    fetched_at: float
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str = ""
    category: str = "standard"
    links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LiveChunk:
    """A relevant live page chunk returned to the answer pipeline."""

    title: str
    text: str
    source: str
    score: float
    fetched_at: float


@dataclass(frozen=True)
class LiveRetrievalResult:
    """Result of one bounded live retrieval attempt."""

    chunks: list[LiveChunk]
    sources: list[str]
    used_live: bool
    error: str | None = None


class NoRedirectHandler(HTTPRedirectHandler):
    """Disable implicit redirects so each hop can be validated."""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Message, newurl: str) -> None:
        """Return no redirected request; callers inspect Location manually."""
        return None


class VisibleHtmlParser(HTMLParser):
    """Extract visible text, page title, metadata, and links from conference HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._current_href: str | None = None
        self._in_title = False
        self.title = ""
        self.lines: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.canonical_url: str | None = None
        self.description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track tags relevant to text extraction and catalog discovery."""
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._current_href = attributes.get("href")
        if tag == "link" and attributes.get("rel") == "canonical":
            self.canonical_url = attributes.get("href")
        if tag == "meta" and (attributes.get("name") or "").lower() == "description":
            self.description = clean_text(attributes.get("content") or "")

    def handle_endtag(self, tag: str) -> None:
        """Close parser state for skipped and linked content."""
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a":
            self._current_href = None

    def handle_data(self, data: str) -> None:
        """Collect visible text and anchor labels."""
        text = clean_text(data)
        if not text:
            return
        if self._in_title:
            self.title += text
            return
        if self._skip_depth:
            return
        self.lines.append(text)
        if self._current_href:
            self.links.append((text, self._current_href))


class ConferencePageCache:
    """Small persistent JSON cache for fetched conference pages."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records = self._load()

    def get(self, url: str) -> PageRecord | None:
        """Return a cached page record if present."""
        payload = self._records.get(url)
        if not payload:
            return None
        try:
            return PageRecord(**payload)
        except TypeError:
            LOGGER.warning("Ignoring invalid cached page metadata for %s.", url)
            return None

    def is_fresh(self, record: PageRecord, ttl_seconds: float) -> bool:
        """Return whether a cached page is still within its TTL."""
        return time.time() - record.fetched_at < ttl_seconds

    def put(self, record: PageRecord) -> None:
        """Store a fetched page record."""
        self._records[record.requested_url] = asdict(record)
        self._save()

    def refresh_timestamp(self, record: PageRecord) -> PageRecord:
        """Refresh a record timestamp after an HTTP 304 response."""
        refreshed = PageRecord(**{**asdict(record), "fetched_at": time.time()})
        self.put(refreshed)
        return refreshed

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Could not read ACSOS live cache at %s.", self.path)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(self._records, indent=2, sort_keys=True), encoding="utf-8")
        temporary_path.replace(self.path)


class ConferencePageFetcher:
    """Fetch and extract allowed ACSOS pages with SSRF and redirect protections."""

    def __init__(self, config: LiveSearchConfig, cache: ConferencePageCache) -> None:
        self.config = config
        self.cache = cache
        self._opener = build_opener(NoRedirectHandler)

    async def fetch(self, url: str, category: str = "standard") -> PageRecord:
        """Fetch a page asynchronously through a bounded thread worker."""
        return await asyncio.to_thread(self.fetch_sync, url, category)

    def fetch_sync(self, url: str, category: str = "standard") -> PageRecord:
        """Fetch one allowed conference page or return a fresh cached record."""
        normalized_url = normalize_candidate_url(url, self.config.base_url, self.config.allowed_hosts)
        if normalized_url is None:
            raise ValueError(f"Blocked unsafe or unsupported ACSOS URL: {url}")
        ttl = ttl_for_category(category, self.config)
        cached = self.cache.get(normalized_url)
        if cached and self.cache.is_fresh(cached, ttl):
            LOGGER.info("ACSOS live cache hit for %s.", normalized_url)
            return cached

        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified

        requested_url = normalized_url
        current_url = normalized_url
        for _ in range(4):
            status, final_url, response_headers, body = self._request_once_with_retry(current_url, headers)
            if status == 304 and cached:
                LOGGER.info("ACSOS live cache revalidated with 304 for %s.", normalized_url)
                return self.cache.refresh_timestamp(cached)
            if status in {301, 302, 303, 307, 308}:
                location = response_headers.get("location")
                if not location:
                    raise RuntimeError(f"Redirect without Location from {current_url}")
                redirected = normalize_candidate_url(urljoin(final_url, location), self.config.base_url, self.config.allowed_hosts)
                if redirected is None:
                    raise ValueError(f"Blocked ACSOS redirect from {current_url} to {location}")
                current_url = redirected
                continue
            if status < 200 or status >= 300:
                raise RuntimeError(f"ACSOS page fetch failed with HTTP {status}: {current_url}")
            content_type = response_headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                raise RuntimeError(f"Unsupported ACSOS content type {content_type!r} for {current_url}")
            text = body.decode("utf-8", errors="replace")
            page = extract_page_record(requested_url, final_url, text, response_headers, category)
            self.cache.put(page)
            LOGGER.info("ACSOS live cache miss fetched %s.", normalized_url)
            return page
        raise RuntimeError(f"Too many redirects while fetching {normalized_url}")

    def _request_once_with_retry(self, url: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str], bytes]:
        """Retry one transient HTTP failure with short backoff."""
        status, final_url, response_headers, body = self._request_once(url, headers)
        if status in TRANSIENT_HTTP_STATUSES:
            time.sleep(0.25)
            status, final_url, response_headers, body = self._request_once(url, headers)
        return status, final_url, response_headers, body

    def _request_once(self, url: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str], bytes]:
        """Perform one HTTP request without following redirects."""
        request = Request(url, headers=headers)
        timeout = max(self.config.connect_timeout_seconds, self.config.read_timeout_seconds)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"ACSOS response exceeds {MAX_RESPONSE_BYTES} bytes: {url}")
                return response.status, response.url, lower_headers(response.headers), body
        except HTTPError as error:
            body = error.read(MAX_RESPONSE_BYTES + 1)
            return error.code, error.url, lower_headers(error.headers), body


class ConferenceSiteSearch:
    """Find the most relevant known ACSOS URLs for a user query."""

    def __init__(self, config: LiveSearchConfig, knowledge: Any | None = None) -> None:
        self.config = config
        self.knowledge = knowledge

    def search(self, query: str) -> list[UrlCandidate]:
        """Rank known ACSOS pages using deterministic lexical scoring."""
        candidates = deduplicate_candidates(self._load_candidates())
        query_terms = set(tokenize(query))
        scored: list[tuple[float, UrlCandidate]] = []
        for candidate in candidates:
            haystack = f"{candidate.title} {candidate.category} {candidate.summary} {candidate.url}"
            haystack_terms = set(tokenize(haystack))
            score = len(query_terms & haystack_terms)
            score += 2 * len(query_terms & set(tokenize(candidate.title)))
            if candidate.category == "dynamic" and query_terms & DYNAMIC_TERMS:
                score += 1
            if score > 0:
                scored.append((float(score), candidate))
        if not scored and query_terms & DYNAMIC_TERMS:
            scored = [(1.0, candidate) for candidate in candidates if candidate.category == "dynamic"]
        ranked = [candidate for _, candidate in sorted(scored, key=lambda item: item[0], reverse=True)]
        return ranked[: self.config.max_search_results]

    def _load_candidates(self) -> list[UrlCandidate]:
        candidates = list(default_catalog_candidates(self.config))
        candidates.extend(load_catalog_file(self.config.catalog_path, self.config))
        if self.knowledge is not None:
            candidates.extend(candidates_from_knowledge(self.knowledge, self.config))
        return candidates


class ConferenceLiveRetriever:
    """Retrieve relevant live ACSOS chunks under strict page and time budgets."""

    def __init__(
        self,
        config: LiveSearchConfig,
        site_search: ConferenceSiteSearch,
        fetcher: ConferencePageFetcher,
        data_path: Path,
    ) -> None:
        self.config = config
        self.site_search = site_search
        self.fetcher = fetcher
        self.data_path = data_path
        self._failure_count = 0
        self._disabled_until = 0.0

    def should_use_live(self, question: str, local_chunks: list[Any]) -> bool:
        """Apply a deterministic policy for deciding whether live retrieval is useful."""
        if not self.config.enabled or self.config.max_live_tool_calls <= 0 or time.monotonic() < self._disabled_until:
            return False
        terms = set(tokenize(question))
        if not local_chunks:
            return True
        dynamic_question = bool(terms & DYNAMIC_TERMS)
        local_age = local_data_age_seconds(self.data_path)
        if dynamic_question and local_age > self.config.cache_ttl_dynamic_seconds:
            return True
        if dynamic_question:
            return True
        return len(local_chunks) < 2

    async def retrieve(self, question: str, local_chunks: list[Any]) -> LiveRetrievalResult:
        """Fetch and rank a small live context for one user question."""
        if not self.should_use_live(question, local_chunks):
            return LiveRetrievalResult(chunks=[], sources=[], used_live=False)
        started = time.monotonic()
        try:
            return await asyncio.wait_for(self._retrieve(question), timeout=self.config.overall_timeout_seconds)
        except Exception as error:
            self._record_failure()
            message = str(error) or error.__class__.__name__
            LOGGER.warning("ACSOS live retrieval failed after %.2fs: %s", time.monotonic() - started, message)
            return LiveRetrievalResult(chunks=[], sources=[], used_live=True, error=message)

    async def _retrieve(self, question: str) -> LiveRetrievalResult:
        candidates = self.site_search.search(question)
        selected = candidates[: self.config.max_pages_per_query]
        if not selected:
            return LiveRetrievalResult(chunks=[], sources=[], used_live=True, error="no relevant ACSOS pages found")
        LOGGER.info("ACSOS live search selected URLs: %s", [candidate.url for candidate in selected])
        tasks = [self.fetcher.fetch(candidate.url, candidate.category) for candidate in selected]
        pages = [page for page in await asyncio.gather(*tasks, return_exceptions=True) if isinstance(page, PageRecord)]
        chunks = rank_live_chunks(question, pages)
        self._failure_count = 0
        sources = sorted({chunk.source for chunk in chunks})
        LOGGER.info(
            "ACSOS live retrieval completed: candidates=%s fetched=%s chunks=%s.",
            len(candidates),
            len(pages),
            len(chunks),
        )
        return LiveRetrievalResult(chunks=chunks, sources=sources, used_live=True)

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= 3:
            self._disabled_until = time.monotonic() + 60.0


def discover_catalog(config: LiveSearchConfig, fetcher: ConferencePageFetcher) -> list[UrlCandidate]:
    """Discover a bounded catalog from the ACSOS home page and known navigation pages."""
    seed_urls = [candidate.url for candidate in default_catalog_candidates(config)]
    discovered: list[UrlCandidate] = []
    link_candidates: list[UrlCandidate] = []
    for url in seed_urls[: config.max_search_results + 8]:
        try:
            page = fetcher.fetch_sync(url, "dynamic")
        except Exception as error:
            LOGGER.warning("Could not discover links from %s: %s", url, error)
            continue
        now = time.time()
        discovered.append(
            UrlCandidate(
                url=page.final_url,
                title=page.title,
                category=classify_url(page.final_url),
                summary=page.text[:500],
                discovered_at=now,
                updated_at=page.fetched_at,
                content_hash=page.content_hash,
            ),
        )
        for label, href in page.links:
            normalized = normalize_candidate_url(href, page.final_url, config.allowed_hosts)
            if normalized is None:
                continue
            link_candidates.append(
                UrlCandidate(
                    url=normalized,
                    title=label or title_from_url(normalized),
                    category=classify_url(normalized),
                    summary=f"Linked from {page.title}: {label}",
                    discovered_at=now,
                ),
            )
    return deduplicate_candidates([*default_catalog_candidates(config), *discovered, *link_candidates])


def write_catalog(path: Path, candidates: list[UrlCandidate]) -> None:
    """Write a URL catalog without triggering full content indexing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": time.time(), "pages": [asdict(candidate) for candidate in candidates]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def extract_page_record(
    requested_url: str,
    final_url: str,
    html: str,
    headers: dict[str, str],
    category: str,
) -> PageRecord:
    """Extract title and main text from one HTML page."""
    parser = VisibleHtmlParser()
    parser.feed(html)
    title = clean_title(parser.title) or "ACSOS 2026"
    lines = strip_boilerplate(parser.lines, title)
    text = "\n".join(lines)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    canonical = parser.canonical_url or final_url
    links = tuple((label, urljoin(final_url, href)) for label, href in parser.links)
    return PageRecord(
        requested_url=requested_url,
        final_url=canonical,
        title=title,
        text=text,
        fetched_at=time.time(),
        etag=headers.get("etag"),
        last_modified=headers.get("last-modified"),
        content_hash=digest,
        category=category,
        links=links,
    )


def strip_boilerplate(lines: list[str], title: str) -> list[str]:
    """Remove repeated navigation, footer, and tiny UI fragments from extracted text."""
    collapsed = deduplicate_text(clean_text(line) for line in lines)
    start = 0
    title_terms = set(tokenize(title))
    for index, line in enumerate(collapsed):
        if title_terms and len(title_terms & set(tokenize(line))) >= min(2, len(title_terms)):
            start = index
            break
    content = collapsed[start:]
    for index, line in enumerate(content):
        if line == "x" or line.startswith("x ") or line == "using" or line.startswith("using "):
            content = content[:index]
            break
    return [
        line
        for line in content
        if line
        and line not in {"Toggle navigation", "Sign in", "Sign up", "Search", "Series"}
        and not line.startswith("Image:")
        and not line.startswith("Photo ")
    ]


def rank_live_chunks(question: str, pages: list[PageRecord]) -> list[LiveChunk]:
    """Split fetched pages and return the highest-scoring non-duplicate chunks."""
    query_terms = set(tokenize(question))
    scored: list[LiveChunk] = []
    seen: set[str] = set()
    budget = LIVE_CONTEXT_BUDGET_CHARS
    for page in pages:
        for text in split_chunks(page.text):
            fingerprint = normalize_for_dedup(text)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            chunk_terms = set(tokenize(f"{page.title} {text}"))
            score = len(query_terms & chunk_terms) + 2 * len(query_terms & set(tokenize(page.title)))
            if score <= 0 and query_terms:
                continue
            scored.append(LiveChunk(page.title, text, page.final_url, float(score), page.fetched_at))
    result: list[LiveChunk] = []
    for chunk in sorted(scored, key=lambda item: item.score, reverse=True):
        if budget <= 0:
            break
        result.append(chunk)
        budget -= len(chunk.text)
    return result[:6]


def split_chunks(text: str) -> list[str]:
    """Split page text into stable chunks without embeddings."""
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}|\n(?=#+\s)|(?<=\.)\s+(?=[A-Z])", text) if paragraph.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= MAX_CHUNK_CHARS:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph[-MAX_CHUNK_CHARS:] if len(paragraph) > MAX_CHUNK_CHARS else paragraph
    if current:
        chunks.append(current)
    if len(chunks) <= 1 and len(text) > MAX_CHUNK_CHARS:
        chunks = [text[index : index + MAX_CHUNK_CHARS] for index in range(0, len(text), MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS)]
    return chunks


def normalize_candidate_url(raw_url: str, base_url: str, allowed_hosts: tuple[str, ...]) -> str | None:
    """Normalize and validate ACSOS URLs before ranking or fetching."""
    parsed = urlparse(urljoin(base_url.rstrip("/") + "/", raw_url))
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts or is_local_or_private_host(host):
        return None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if host == "conf.researchr.org" and "acsos-2026" not in path:
        return None
    if any(path.startswith(part) or f"/{part.strip('/')}/" in path for part in DISALLOWED_PATH_PARTS):
        return None
    if any(path.startswith(prefix) for prefix in DISALLOWED_PATH_PREFIXES):
        return None
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in ALLOWED_PATH_PREFIXES):
        return None
    if re.search(r"\.(css|gif|ico|jpeg|jpg|js|pdf|png|svg|webp)$", path, flags=re.IGNORECASE):
        return None
    if parsed.query:
        return None
    normalized = parsed._replace(netloc=host, path=path.rstrip("/") if path != "/" else "/", params="", query="", fragment="")
    return urlunparse(normalized)


def default_catalog_candidates(config: LiveSearchConfig) -> list[UrlCandidate]:
    """Return built-in ACSOS pages identified during site analysis."""
    now = time.time()
    return [
        UrlCandidate(
            url=normalize_candidate_url(url, config.base_url, config.allowed_hosts) or url,
            title=title,
            category=category,
            summary=summary,
            discovered_at=now,
        )
        for url, title, category, summary in DEFAULT_CATALOG_SEEDS
    ]


def candidates_from_knowledge(knowledge: Any, config: LiveSearchConfig) -> list[UrlCandidate]:
    """Build URL candidates from the existing local conference JSON."""
    data = getattr(knowledge, "data", {})
    candidates: list[UrlCandidate] = []
    program = data.get("program", {})
    program_summary = " ".join(
        [
            program.get("status", ""),
            *(
                f"{day.get('day', '')} {day.get('date', '')} "
                + " ".join(
                    f"{entry.get('time', '')} {entry.get('title', '')} "
                    f"{entry.get('details', '')} {entry.get('category', '')}"
                    for entry in day.get("entries", [])
                )
                for day in program.get("days", [])
            ),
        ],
    )
    add_candidate(
        candidates,
        program.get("url"),
        program.get("title", "Program at a Glance"),
        "dynamic",
        program_summary,
        config,
    )
    for page in data.get("infoPages", []):
        add_candidate(candidates, page.get("url"), page.get("title", ""), "standard", page.get("body", ""), config)
    for track in data.get("tracks", []):
        add_candidate(candidates, track.get("url"), track.get("name", ""), "dynamic", track.get("summary", ""), config)
    for keynote in data.get("keynotes", []):
        add_candidate(candidates, keynote.get("url"), "Keynotes", "dynamic", keynote.get("title", ""), config)
    for person in data.get("committees", []):
        add_candidate(candidates, person.get("url"), person.get("role", ""), "standard", person.get("name", ""), config)
    return candidates


def load_catalog_file(path: Path, config: LiveSearchConfig) -> list[UrlCandidate]:
    """Load a pre-discovered URL catalog if present."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read ACSOS catalog at %s.", path)
        return []
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    candidates: list[UrlCandidate] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        add_candidate(
            candidates,
            page.get("url"),
            page.get("title", ""),
            page.get("category", "standard"),
            page.get("summary", ""),
            config,
            page.get("discovered_at", 0.0),
            page.get("updated_at", 0.0),
            page.get("content_hash", ""),
        )
    return candidates


def add_candidate(
    candidates: list[UrlCandidate],
    url: str | None,
    title: str,
    category: str,
    summary: str,
    config: LiveSearchConfig,
    discovered_at: float = 0.0,
    updated_at: float = 0.0,
    content_hash: str = "",
) -> None:
    """Append a valid URL candidate."""
    if not url:
        return
    normalized = normalize_candidate_url(url, config.base_url, config.allowed_hosts)
    if normalized is None:
        return
    candidates.append(UrlCandidate(normalized, title, category, summary, discovered_at, updated_at, content_hash))


def deduplicate_candidates(candidates: list[UrlCandidate]) -> list[UrlCandidate]:
    """Preserve the first candidate for each normalized URL."""
    seen = set()
    result = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result


def ttl_for_category(category: str, config: LiveSearchConfig) -> float:
    """Return configured TTL for a page category."""
    if category == "dynamic":
        return config.cache_ttl_dynamic_seconds
    if category == "static":
        return config.cache_ttl_static_seconds
    return config.cache_ttl_standard_seconds


def classify_url(url: str) -> str:
    """Classify a URL for cache TTL selection."""
    path = urlparse(url).path.casefold()
    if any(part in path for part in ("dates", "news", "registration", "program", "track", "social", "keynotes")):
        return "dynamic"
    if any(part in path for part in ("code-of-conduct", "visit-cesena", "visa")):
        return "static"
    return "standard"


def title_from_url(url: str) -> str:
    """Create a readable title fallback from an ACSOS URL path."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "ACSOS 2026"
    return path.rsplit("/", maxsplit=1)[-1].replace("-", " ").replace("_", " ").title()


def lower_headers(headers: Message) -> dict[str, str]:
    """Return a case-insensitive-friendly header dictionary."""
    return {key.lower(): value for key, value in headers.items()}


def clean_text(text: str) -> str:
    """Normalize whitespace in scraped text."""
    return re.sub(r"\s+", " ", text).strip()


def clean_title(title: str) -> str:
    """Remove repeated conference suffixes from page titles."""
    return re.sub(r"\s+-\s+ACSOS 2026$", "", clean_text(title)).strip()


def deduplicate_text(lines: Any) -> list[str]:
    """Drop adjacent and global duplicate text lines while preserving order."""
    seen = set()
    result = []
    previous = ""
    for line in lines:
        if not line or line == previous or line in seen:
            previous = line
            continue
        seen.add(line)
        result.append(line)
        previous = line
    return result


def tokenize(text: str) -> list[str]:
    """Split text into lowercase searchable terms."""
    normalized = text.casefold().replace("-", " ").replace(":", " ")
    return [term for term in re.findall(r"[a-z0-9]+", normalized) if term not in STOPWORDS]


def normalize_for_dedup(text: str) -> str:
    """Normalize chunk text for duplicate suppression."""
    return " ".join(tokenize(text[:700]))


def is_local_or_private_host(host: str) -> bool:
    """Reject local or private IP literals and local hostnames."""
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def local_data_age_seconds(path: Path) -> float:
    """Return the age of the local conference data file."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return float("inf")


def parse_bool_env(name: str, default: bool) -> bool:
    """Read a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def parse_int_env(name: str, default: int) -> int:
    """Read an integer environment variable with a safe fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        LOGGER.warning("Invalid %s; using %s.", name, default)
        return default


def parse_float_env(name: str, default: float) -> float:
    """Read a float environment variable with a safe fallback."""
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        LOGGER.warning("Invalid %s; using %s.", name, default)
        return default
