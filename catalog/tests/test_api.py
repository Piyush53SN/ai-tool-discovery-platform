"""API tests for /api/tools/ (list, filters, search, detail, compare, view)."""
import pytest

from catalog.models import Tool

pytestmark = pytest.mark.django_db

TOOLS_URL = "/api/tools/"


def make_sample_catalog(make_category, make_tool, make_tag):
    writing = make_category("writing")
    video = make_category("video-audio")
    tag_seo, tag_free = make_tag("seo"), make_tag("free-tier")
    tools = {
        "alpha": make_tool("Alpha Writer", writing, tags=[tag_seo], pricing="free"),
        "beta": make_tool("Beta Coder", make_category("code-development")),
        "gamma": make_tool("Gamma Voice", video, tags=[tag_free], pricing="paid"),
    }
    return tools


class TestToolList:
    def test_pagination_shape(self, api, category, make_tool):
        for i in range(15):
            make_tool(f"Tool {i}", category)
        response = api.get(TOOLS_URL)
        assert response.status_code == 200
        body = response.json()
        assert set(body) >= {"count", "pages", "next", "previous", "results"}
        assert body["count"] == 15
        assert len(body["results"]) == 12  # default page size

    def test_filter_by_category(self, api, make_category, make_tool, make_tag):
        make_sample_catalog(make_category, make_tool, make_tag)
        response = api.get(TOOLS_URL, {"category": "writing"})
        names = [t["name"] for t in response.json()["results"]]
        assert names == ["Alpha Writer"]

    def test_filter_by_pricing_multi(self, api, make_category, make_tool, make_tag):
        make_sample_catalog(make_category, make_tool, make_tag)
        response = api.get(TOOLS_URL, {"pricing": "free,paid"})
        names = {t["name"] for t in response.json()["results"]}
        assert names == {"Alpha Writer", "Gamma Voice"}

    def test_filter_by_tags(self, api, make_category, make_tool, make_tag):
        make_sample_catalog(make_category, make_tool, make_tag)
        response = api.get(TOOLS_URL, {"tags": "seo"})
        assert [t["name"] for t in response.json()["results"]] == ["Alpha Writer"]

    def test_filter_by_min_rating(self, api, category, make_tool):
        low = make_tool("Low Star", category)
        high = make_tool("High Star", category)
        Tool.objects.filter(pk=high.pk).update(avg_rating=4.6)
        Tool.objects.filter(pk=low.pk).update(avg_rating=2.9)
        response = api.get(TOOLS_URL, {"min_rating": "4.0"})
        assert [t["name"] for t in response.json()["results"]] == ["High Star"]

    def test_ordering(self, api, category, make_tool):
        older = make_tool("Older", category)
        newer = make_tool("Newer", category)
        Tool.objects.filter(pk=newer.pk).update(created_at="2026-01-02T00:00:00Z")
        Tool.objects.filter(pk=older.pk).update(created_at="2026-01-01T00:00:00Z")
        response = api.get(TOOLS_URL, {"ordering": "created_at"})
        assert [t["name"] for t in response.json()["results"]][:2] == ["Older", "Newer"]


class TestHybridSearch:
    def test_exact_name_hit_ranks_top(self, api, category, make_category, make_tool):
        make_tool("QuillBot", category, description="paraphrasing tool")
        make_tool("Unrelated Kafka Thing", make_category("data-analytics"))
        response = api.get(TOOLS_URL, {"search": "QuillBot"})
        body = response.json()
        assert body["search"]["query"] == "QuillBot"
        assert body["results"][0]["name"] == "QuillBot"

    def test_semantic_overlap_beats_disjoint_text(self, api, category, make_tool):
        """The hash backend gives lexical overlap; hybrid search must surface
        vocabulary overlap over completely unrelated descriptions."""
        match = make_tool(
            "DraftHelper", category,
            description="AI assistant that writes blog posts and marketing copy for seo",
        )
        make_tool(
            "Noise", category,
            description="manages postgres database migrations and vector indexes",
        )
        response = api.get(TOOLS_URL, {"search": "ai writing blog posts seo"})
        assert response.json()["results"][0]["id"] == match.pk

    def test_search_composes_with_facets(self, api, category, make_tool):
        match = make_tool("SeoTool", category, pricing="free",
                          description="search engine optimisation copy")
        make_tool("SeoToolPro", category, pricing="paid",
                  description="search engine optimisation copy")
        response = api.get(TOOLS_URL, {"search": "search engine optimisation",
                                       "pricing": "free"})
        results = response.json()["results"]
        assert [r["id"] for r in results] == [match.pk]


class TestToolDetail:
    def test_detail_returns_similar_tools(self, api, category, make_tool):
        from conftest import unit_vector

        base = make_tool("Base", category, embedding=unit_vector(0))
        twin = make_tool("Twin", category, embedding=unit_vector(0))
        far = make_tool("Far", category, embedding=unit_vector(1))
        response = api.get(f"{TOOLS_URL}{base.slug}/")
        assert response.status_code == 200
        similar = response.json()["similar_tools"]
        assert similar and similar[0]["id"] == twin.pk  # nearest by cosine
        assert similar[0]["id"] != far.pk

    def test_404_for_unknown_slug(self, api):
        assert api.get(f"{TOOLS_URL}nope/").status_code == 404


class TestCompareEndpoint:
    def test_compare_matrix(self, api, category, make_tool):
        a = make_tool("Cmp A", category, pricing="free")
        b = make_tool("Cmp B", category, pricing="paid")
        response = api.post("/api/tools/compare/", {"tool_ids": [a.pk, b.pk]}, format="json")
        assert response.status_code == 200
        body = response.json()
        assert [t["name"] for t in body["tools"]] == ["Cmp A", "Cmp B"]
        pricing = next(r for r in body["rows"] if r["attribute"] == "pricing")
        assert pricing["values"] == ["Free", "Paid"]

    def test_compare_rejects_one_tool(self, api, category, make_tool):
        a = make_tool("Solo", category)
        response = api.post("/api/tools/compare/", {"tool_ids": [a.pk]}, format="json")
        assert response.status_code == 400

    def test_compare_rejects_unknown_tool(self, api):
        response = api.post("/api/tools/compare/", {"tool_ids": [901, 902]}, format="json")
        assert response.status_code == 400


class TestViewTracking:
    def test_authenticated_view_recorded(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Looked At", category)
        response = client.post(f"{TOOLS_URL}{tool.slug}/view/")
        assert response.status_code == 200
        assert response.json()["recorded"] is True
        from interactions.models import Interaction
        assert Interaction.objects.filter(user=user, tool=tool, type="view").exists()

    def test_anonymous_view_not_recorded(self, api, category, make_tool):
        tool = make_tool("Anon View", category)
        response = api.post(f"{TOOLS_URL}{tool.slug}/view/")
        assert response.status_code == 200
        assert response.json()["recorded"] is False
