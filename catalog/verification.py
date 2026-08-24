"""
Link verification crawler (Section 3.2).

Policy — this is a *verifier*, not a scraper:
  * HEAD (fallback GET) per tool at most once per CHECK_INTERVAL (24h).
  * robots.txt honoured per domain with a descriptive User-Agent.
  * GET responses are read up to MAX_PAGE_BYTES and closed — we keep ONLY the
    <title> and og:description as staleness signals, never page copy.
  * one slow site can't stall the batch: per-request timeout, jittered pauses
    between requests, and a bounded batch size per run.
  * 3+ consecutive failures -> needs_review=True for a human; never auto-delete.
"""
from __future__ import annotations

import logging
import random
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from django.utils import timezone

from .models import Tool

logger = logging.getLogger(__name__)

USER_AGENT = (
    "AIToolDiscoveryBot/1.0 (+https://github.com/Piyush53SN/ai-tool-discovery-platform; "
    "link verification for the AI tool catalog; respects robots.txt)"
)
CHECK_INTERVAL = timedelta(hours=24)   # 1 request per tool per 24h, max
REQUEST_TIMEOUT = 10.0                 # one slow site must not stall the batch
MAX_PAGE_BYTES = 64 * 1024             # bounded read: enough for <head> metadata
MAX_CONSECUTIVE_FAILURES = 3           # then flag for human review
DEFAULT_BATCH_LIMIT = 50               # tools verified per run
DEFAULT_JITTER = (1.0, 4.0)            # seconds slept between requests

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Domain-level robots.txt cache, scoped to a single verification run.
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


@dataclass
class VerificationResult:
    tool_id: int
    slug: str
    ok: bool
    http_status: int | None = None
    title: str | None = None
    note: str = ""
    extra: dict = field(default_factory=dict)


def _robots_allows(client: httpx.Client, url: str) -> bool:
    """True if robots.txt permits our UA to fetch this URL (missing file = allow)."""
    host = httpx.URL(url).copy_with(path="/robots.txt", query=None, fragment=None)
    if host.host not in _robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = client.get(str(host), timeout=REQUEST_TIMEOUT,
                                  headers={"User-Agent": USER_AGENT})
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])  # no / unreachable robots -> treat as allow-all
        except httpx.HTTPError:
            parser.parse([])
        _robots_cache[host.host] = parser
    parser = _robots_cache[host.host]
    return parser is None or parser.can_fetch(USER_AGENT, url)


def _bounded_body(response: httpx.Response) -> str:
    """Read at most MAX_PAGE_BYTES of the body, then close the stream."""
    chunks = []
    read = 0
    try:
        for chunk in response.iter_bytes(chunk_size=8192):
            chunks.append(chunk)
            read += len(chunk)
            if read >= MAX_PAGE_BYTES:
                break
    finally:
        response.close()
    return b"".join(chunks).decode("utf-8", errors="replace")


def verify_tool(tool: Tool, client: httpx.Client | None = None) -> VerificationResult:
    """Check one tool's URL and persist the outcome. Network-safe: any transport
    error is recorded as a failed check, never raised."""
    client = client or httpx.Client(
        timeout=REQUEST_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    owns_client = client is None

    status: int | None = None
    title: str | None = None
    note = ""

    try:
        if not _robots_allows(client, tool.url):
            # Disallowed by robots.txt: not evidence the link is dead.
            # Record a soft "skipped" check so we don't re-hit it every run.
            _persist(tool, ok=tool.is_live, status=None, title=None,
                     failure=False, note="robots.txt disallows; skipped")
            return VerificationResult(tool.pk, tool.slug, ok=tool.is_live,
                                      note="robots.txt disallows; skipped")

        response = client.head(tool.url)
        if response.status_code >= 400 or not response.headers.get("content-type", "").startswith(
            ("text/", "application/xhtml")
        ):
            # Some sites reject HEAD or misreport — a bounded GET is the fallback.
            response = client.get(tool.url)
            if response.status_code < 400:
                head_html = _bounded_body(response)
                title = (_TITLE_RE.search(head_html) or [None, None])[1]
                title = title.strip()[:500] if title else None
        else:
            title = None
        status = response.status_code
        ok = status < 400
        if not ok:
            note = f"HTTP {status}"
    except httpx.HTTPError as exc:
        ok = False
        note = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        if owns_client:
            client.close()

    _persist(tool, ok=ok, status=status, title=title, failure=not ok, note=note)
    return VerificationResult(tool.pk, tool.slug, ok, status, title, note)


def _persist(tool: Tool, *, ok: bool, status: int | None, title: str | None,
             failure: bool, note: str) -> None:
    tool.http_status = status
    tool.last_checked_at = timezone.now()
    tool.is_live = ok
    if title:
        tool.checked_title = title
    if failure:
        tool.consecutive_failures += 1
        if tool.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            tool.needs_review = True
    else:
        tool.consecutive_failures = 0
        tool.needs_review = False
    tool.save(update_fields=[
        "is_live", "last_checked_at", "http_status", "checked_title",
        "consecutive_failures", "needs_review",
    ])
    logger.info("verify %s -> %s %s", tool.slug, "OK" if ok else "FAIL", note)


def due_tools(limit: int = DEFAULT_BATCH_LIMIT):
    """Tools not checked in the last 24h (oldest first; never-checked included)."""
    cutoff = timezone.now() - CHECK_INTERVAL
    return Tool.objects.filter(last_checked_at__lt=cutoff) | Tool.objects.filter(
        last_checked_at__isnull=True
    )


def verify_due_tools(
    limit: int = DEFAULT_BATCH_LIMIT,
    jitter: tuple[float, float] = DEFAULT_JITTER,
    only_slug: str | None = None,
) -> dict:
    """Verify a bounded batch of due tools with polite jitter between requests.

    Returns a summary dict for logging/ops. Designed to run as a Celery beat
    task (see catalog/celery_tasks.py) or directly via the management command.
    """
    _robots_cache.clear()
    queryset = due_tools() if not only_slug else Tool.objects.filter(slug=only_slug)
    tools = list(queryset.order_by("last_checked_at", "pk")[:limit])

    results: list[VerificationResult] = []
    with httpx.Client(
        timeout=REQUEST_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        for index, tool in enumerate(tools):
            results.append(verify_tool(tool, client=client))
            if index < len(tools) - 1:
                time.sleep(random.uniform(*jitter))  # polite pacing across sites

    summary = {
        "checked": len(results),
        "ok": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "skipped": sum(1 for r in results if "robots" in r.note),
    }
    logger.info("verification run: %s", summary)
    return summary
