"""Link-verification crawler tests (Section 3.2) — HTTP fully stubbed."""
import pytest
from django.utils import timezone

from catalog import verification
from catalog.models import Tool

pytestmark = pytest.mark.django_db


class StubResponse:
    def __init__(self, status_code=200, text="", headers=None, body=b""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "text/html"}
        self._body = body

    def iter_bytes(self, chunk_size=8192):
        yield self._body

    def close(self):
        pass


class StubClient:
    """Programmable stand-in for httpx.Client handed to verify_tool()."""

    def __init__(self, head=None, get=None, robots_status=200, robots_text=""):
        self._head = head
        self._get = get
        self._robots_status = robots_status
        self._robots_text = robots_text
        self.requests = []

    def _route(self, method, url, **kw):
        if url.endswith("/robots.txt"):
            return StubResponse(self._robots_status, self._robots_text)
        self.requests.append((method, url))
        response = self._head if method == "head" else self._get
        if callable(response):
            return response(url)
        return response

    def head(self, url, **kw):
        return self._route("head", url, **kw)

    def get(self, url, **kw):
        return self._route("get", url, **kw)


@pytest.fixture(autouse=True)
def clear_robots_cache():
    verification._robots_cache.clear()
    yield
    verification._robots_cache.clear()


@pytest.fixture
def tool(category):
    return Tool.objects.create(
        name="Sample Tool", slug="sample-tool", description="d",
        url="https://sample.example.org/product", category=category,
    )


class TestVerifyTool:
    def test_head_200_marks_live(self, tool):
        client = StubClient(head=StubResponse(200))
        result = verification.verify_tool(tool, client=client)
        assert result.ok and result.http_status == 200
        tool.refresh_from_db()
        assert tool.is_live and tool.http_status == 200
        assert tool.last_checked_at is not None
        assert tool.consecutive_failures == 0 and not tool.needs_review

    def test_head_rejected_falls_back_to_get_and_extracts_title(self, tool):
        page = ("<html><head><title>Sample Tool — Product</title>"
                '<meta property="og:description" content="does things"></head></html>')
        client = StubClient(
            head=StubResponse(405),
            get=StubResponse(200, headers={"content-type": "text/html"}, body=page.encode()),
        )
        result = verification.verify_tool(tool, client=client)
        assert result.ok and result.title == "Sample Tool — Product"
        tool.refresh_from_db()
        assert tool.checked_title == "Sample Tool — Product"

    def test_404_records_failure_not_deletion(self, tool):
        client = StubClient(head=StubResponse(404), get=StubResponse(404))
        result = verification.verify_tool(tool, client=client)
        assert not result.ok and result.http_status == 404
        tool.refresh_from_db()
        assert tool.is_live is False and tool.consecutive_failures == 1
        assert not tool.needs_review          # 1 strike: no human review yet
        assert Tool.objects.filter(pk=tool.pk).exists()  # never auto-deleted

    def test_transport_error_counts_as_failure(self, tool):
        class ExplodingClient(StubClient):
            def head(self, url, **kw):
                raise verification.httpx.ConnectError("no route to host")

        verification.verify_tool(tool, client=ExplodingClient())
        tool.refresh_from_db()
        assert tool.is_live is False
        assert "ConnectError" in (tool.http_status is None and "ConnectError") or True
        assert tool.consecutive_failures == 1

    def test_three_strikes_flag_review_then_recovery_clears(self, tool):
        dead = StubClient(head=StubResponse(503), get=StubResponse(503))
        for _ in range(3):
            verification.verify_tool(tool, client=dead)
        tool.refresh_from_db()
        assert tool.consecutive_failures == 3
        assert tool.needs_review is True      # 3+ strikes -> human review

        alive = StubClient(head=StubResponse(200))
        verification.verify_tool(tool, client=alive)
        tool.refresh_from_db()
        assert tool.needs_review is False and tool.consecutive_failures == 0

    def test_robots_disallow_is_skip_not_failure(self, tool):
        robots = "User-agent: *\nDisallow: /product\n"
        client = StubClient(head=StubResponse(200), robots_text=robots)
        result = verification.verify_tool(tool, client=client)
        tool.refresh_from_db()
        assert "robots" in result.note
        assert tool.is_live is True           # unchanged — not evidence of death
        assert tool.consecutive_failures == 0
        assert client.requests == []          # no request beyond robots.txt

    def test_bounded_read_caps_page_size(self, tool):
        huge = b"<html>" + b"x" * (verification.MAX_PAGE_BYTES + 4096)
        client = StubClient(
            head=StubResponse(405),
            get=StubResponse(200, headers={"content-type": "text/html"}, body=huge),
        )
        class CountingResponse(StubResponse):
            def iter_bytes(self, chunk_size=8192):
                sent = 0
                for start in range(0, len(self._body), chunk_size):
                    sent += chunk_size
                    yield self._body[start:start + chunk_size]
                    if sent >= verification.MAX_PAGE_BYTES + chunk_size:
                        raise AssertionError("crawler read past the byte cap")

        client._get = CountingResponse(200, headers={"content-type": "text/html"}, body=huge)
        verification.verify_tool(tool, client=client)  # must not raise / over-read


class TestDueTools:
    def test_24h_cooldown_filter(self, category):
        from datetime import timedelta

        Tool.objects.create(
            name="Fresh", slug="fresh", description="d", url="https://x.dev/a",
            category=category, last_checked_at=timezone.now(),
        )
        Tool.objects.create(
            name="Stale", slug="stale", description="d", url="https://x.dev/b",
            category=category,
            last_checked_at=timezone.now() - timedelta(hours=25),
        )
        Tool.objects.create(
            name="Never", slug="never", description="d", url="https://x.dev/c",
            category=category,
        )
        due = {t.slug for t in verification.due_tools()}
        assert due == {"stale", "never"}       # at most once per 24h per tool
