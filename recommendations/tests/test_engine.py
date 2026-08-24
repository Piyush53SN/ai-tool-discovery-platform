"""
Recommendation engine tests — the graded centerpiece (Section 8).

These tests pin a small set of HAND-CHECKABLE fake embeddings (axis-aligned
unit vectors in 384-dim space) and assert that:

  * the preference vector is exactly the weight-weighted mean of interacted
    tools' embeddings (weights: view=1, bookmark=3, review=4, review>=4 -> 5),
  * candidate ranking follows cosine similarity to that vector,
  * bookmarked tools are excluded,
  * the MMR diversity re-rank trades a little similarity for variety,
  * cold start falls back to onboarding tags, then Bayesian popularity.

With orthogonal unit vectors every number below is exact trigonometry, e.g.
    bookmark unit(0) + view unit(1)  ->  pref = (3*x + 1*y) / sqrt(10)
    sim(pref, x-axis) = 3/sqrt(10) ~= 0.9487
    sim(pref, y-axis) = 1/sqrt(10) ~= 0.3162
"""
import math

import pytest

from conftest import scaled_vector, unit_vector
from interactions.models import Bookmark, Interaction, Review
from recommendations.services import (
    recommend_for_user,
    recompute_preference_vector,
)

pytestmark = pytest.mark.django_db

SQRT10 = math.sqrt(10)
SQRT34 = math.sqrt(34)


# ---------------------------------------------------------------------------
# Preference vector maths
# ---------------------------------------------------------------------------
class TestPreferenceVector:
    def test_weighted_average_of_embeddings(self, api_user, category, make_tool):
        user, _ = api_user
        x_tool = make_tool("X Tool", category, embedding=unit_vector(0))
        y_tool = make_tool("Y Tool", category, embedding=unit_vector(1))

        # Interactions carry the weights (the API service layer writes these
        # rows together with the bookmark; engine tests write the log directly
        # so only the maths is under test).
        Bookmark.objects.create(user=user, tool=x_tool)
        Interaction.objects.create(user=user, tool=x_tool, type="bookmark", weight=3.0)
        Interaction.objects.create(user=user, tool=y_tool, type="view", weight=1.0)

        vector = recompute_preference_vector(user)
        assert vector is not None
        expected_x, expected_y = 3 / SQRT10, 1 / SQRT10
        assert vector[0] == pytest.approx(expected_x, abs=1e-5)
        assert vector[1] == pytest.approx(expected_y, abs=1e-5)

    def test_review_weights_flow_including_enthusiasm_bonus(
        self, api_user, category, make_tool
    ):
        user, _ = api_user
        x_tool = make_tool("X Tool", category, embedding=unit_vector(0))
        y_tool = make_tool("Y Tool", category, embedding=unit_vector(1))

        Interaction.objects.create(user=user, tool=x_tool, type="view", weight=1.0)
        Review.objects.create(user=user, tool=y_tool, rating=5)  # weight 5.0
        Interaction.objects.create(user=user, tool=y_tool, type="review", weight=5.0)

        vector = recompute_preference_vector(user)
        # pref = (1*x + 5*y)/sqrt(26)
        assert vector[0] == pytest.approx(1 / math.sqrt(26), abs=1e-5)
        assert vector[1] == pytest.approx(5 / math.sqrt(26), abs=1e-5)

    def test_no_interactions_clears_vector(self, api_user):
        user, _ = api_user
        assert recompute_preference_vector(user) is None
        assert user.profile.preference_vector is None

    def test_tools_without_embeddings_are_skipped(self, api_user, category, make_tool):
        user, _ = api_user
        embedded = make_tool("Embedded", category, embedding=unit_vector(3))
        plain = make_tool("Plain", category)  # hash-embedded by the signal...
        Tool = type(plain)
        Tool.objects.filter(pk=plain.pk).update(embedding=None)  # simulate "pending"
        for tool in (embedded, plain):
            Bookmark.objects.create(user=user, tool=tool)
            Interaction.objects.create(user=user, tool=tool, type="bookmark", weight=3.0)

        vector = recompute_preference_vector(user)
        assert vector is not None
        assert vector[3] == pytest.approx(1.0, abs=1e-5)  # pure unit(3)


# ---------------------------------------------------------------------------
# Ranking behaviour
# ---------------------------------------------------------------------------
class TestRanking:
    def make_world(self, api_user, category, make_tool):
        """Preference = (3*x + 5*y)/sqrt(34): bookmark on x, 5-star review on y."""
        user, _ = api_user
        x_tool = make_tool("Bookmarked X", category, embedding=unit_vector(0))
        y_tool = make_tool("Reviewed Y", category, embedding=unit_vector(1))
        Bookmark.objects.create(user=user, tool=x_tool)
        Interaction.objects.create(user=user, tool=x_tool, type="bookmark", weight=3.0)
        Review.objects.create(user=user, tool=y_tool, rating=5)
        Interaction.objects.create(user=user, tool=y_tool, type="review", weight=5.0)

        candidates = {
            "x_like": make_tool("Candidate X-Like", category, embedding=unit_vector(0)),
            "y_like": make_tool("Candidate Y-Like", category, embedding=unit_vector(1)),
            "neutral": make_tool("Candidate Neutral", category, embedding=unit_vector(2)),
            "opposite": make_tool("Candidate Opposite", category,
                                  embedding=scaled_vector(-1.0, 0.0)),
        }
        return user, x_tool, y_tool, candidates

    def test_cosine_ranking_matches_hand_computed_similarity(
        self, api_user, category, make_tool
    ):
        user, x_tool, y_tool, candidates = self.make_world(api_user, category, make_tool)

        ranked, strategy = recommend_for_user(user, limit=5, diversify=False)
        assert strategy == "personalised"

        sims = {r.tool.name: r.similarity for r in ranked}
        # reviewed (not bookmarked) tools stay eligible:
        assert sims["Reviewed Y"] == pytest.approx(5 / SQRT34, abs=1e-4)
        assert sims["Candidate Y-Like"] == pytest.approx(5 / SQRT34, abs=1e-4)
        assert sims["Candidate X-Like"] == pytest.approx(3 / SQRT34, abs=1e-4)
        assert sims["Candidate Neutral"] == pytest.approx(0.0, abs=1e-4)
        assert sims["Candidate Opposite"] == pytest.approx(-3 / SQRT34, abs=1e-4)

        order = [r.tool.name for r in ranked]
        assert order.index("Candidate Y-Like") < order.index("Candidate X-Like")
        assert order.index("Candidate X-Like") < order.index("Candidate Neutral")
        assert order.index("Candidate Neutral") < order.index("Candidate Opposite")

    def test_bookmarked_tools_excluded_from_recommendations(
        self, api_user, category, make_tool
    ):
        user, x_tool, y_tool, candidates = self.make_world(api_user, category, make_tool)
        ranked, _ = recommend_for_user(user, limit=10, diversify=False)
        names = [r.tool.name for r in ranked]
        assert "Bookmarked X" not in names  # exclusion applies
        assert "Reviewed Y" in names  # reviews do not exclude (spec: bookmarks only)

    def test_weights_change_the_ranking(self, api_user, category, make_tool):
        """Same world, but swap the weights: bookmark y (3.0) + weak view on x
        (1.0) -> preference tips toward y even harder, and the x-like
        candidate must fall behind the y-like one. Mirrors the primary test
        with the axes flipped to prove weights (not insertion order) drive it."""
        user, _ = api_user
        y_tool = make_tool("Y Prime", category, embedding=unit_vector(1))
        x_tool = make_tool("X Weak", category, embedding=unit_vector(0))
        Bookmark.objects.create(user=user, tool=y_tool)
        Interaction.objects.create(user=user, tool=y_tool, type="bookmark", weight=3.0)
        Interaction.objects.create(user=user, tool=x_tool, type="view", weight=1.0)

        make_tool("X Cand", category, embedding=unit_vector(0))
        make_tool("Y Cand", category, embedding=unit_vector(1))
        ranked, _ = recommend_for_user(user, limit=4, diversify=False)
        sims = {r.tool.slug: r.similarity for r in ranked}
        assert sims["y-cand"] == pytest.approx(3 / SQRT10, abs=1e-4)
        assert sims["x-cand"] == pytest.approx(1 / SQRT10, abs=1e-4)


# ---------------------------------------------------------------------------
# MMR diversity re-rank (stretch goal)
# ---------------------------------------------------------------------------
class TestDiversity:
    def test_mmr_promotes_different_category_over_near_duplicate(
        self, api_user, make_category, make_tool
    ):
        user, _ = api_user
        home = make_category("home")
        away = make_category("away")

        anchor = make_tool("Anchor", home, embedding=unit_vector(0))
        Bookmark.objects.create(user=user, tool=anchor)
        Interaction.objects.create(user=user, tool=anchor, type="bookmark", weight=3.0)

        make_tool("Near Duplicate", home,
                             embedding=scaled_vector(0.99, 0.141067))  # sim ~0.99
        make_tool("Different", away,
                              embedding=scaled_vector(0.80, 0.60))  # sim 0.80

        diverse_ranked, strategy = recommend_for_user(user, limit=2, diversify=True)
        assert strategy == "personalised_diverse"
        order = [r.tool.name for r in diverse_ranked]
        assert order == ["Near Duplicate", "Different"]

        plain_ranked, _ = recommend_for_user(user, limit=2, diversify=False)
        assert [r.tool.name for r in plain_ranked] == ["Near Duplicate", "Different"]

    def test_mmr_penalty_reorders_when_categories_collide(
        self, api_user, make_category, make_tool
    ):
        """Three same-category candidates with sims 1.0 / 0.99 / 0.80 and one
        other-category candidate with sim 0.5: pure similarity puts OtherTool
        last; MMR must hoist it above the redundant third same-category tool
        once its jaccard penalty is paid."""
        user, _ = api_user
        same = make_category("same")
        other = make_category("other")

        anchor = make_tool("Anchor", same, embedding=unit_vector(0))
        Bookmark.objects.create(user=user, tool=anchor)
        Interaction.objects.create(user=user, tool=anchor, type="bookmark", weight=3.0)

        make_tool("Twin", same, embedding=scaled_vector(0.99, 0.141067))
        make_tool("Third", same, embedding=scaled_vector(0.80, 0.60))
        make_tool("Outsider", other, embedding=scaled_vector(0.50, 0.8660254))

        # lambda = 0.7 (settings): after Twin is picked,
        #   MMR(Third)    = 0.7*0.80 - 0.3*1.0 = 0.26
        #   MMR(Outsider) = 0.7*0.50 - 0.3*0.0 = 0.35   <- wins
        ranked, strategy = recommend_for_user(user, limit=3, diversify=True)
        assert strategy == "personalised_diverse"
        assert [r.tool.name for r in ranked] == ["Twin", "Outsider", "Third"]


# ---------------------------------------------------------------------------
# Cold start (Section 6.5.4)
# ---------------------------------------------------------------------------
class TestColdStart:
    def test_no_interactions_uses_onboarding_tags(self, api_user, make_category,
                                                  make_tool, make_tag):
        user, client = api_user
        seo = make_tag("seo")
        writing, music = make_category("writing"), make_category("music-gen")
        make_tool("SEO Writer", writing, tags=[seo])
        make_tool("Music Maker", music)

        user.profile.onboarding_tags.set([seo])
        ranked, strategy = recommend_for_user(user, limit=5)
        assert strategy == "cold_start_tags"
        names = [r.tool.name for r in ranked]
        assert names == ["SEO Writer"]  # only tag-matching tools surface

    def test_no_tags_uses_bayesian_popularity(self, api_user, category, make_tool):
        """A 4.3 average over 12 reviews with bookmarks must beat a single
        5-star review with none (Bayesian smoothing + traction)."""
        from catalog.models import Tool as ToolModel

        user, _ = api_user
        strong = make_tool("Strong", category)
        lucky = make_tool("Lucky", category)
        ToolModel.objects.filter(pk=strong.pk).update(
            avg_rating=4.3, rating_count=12, bookmark_count=5
        )
        ToolModel.objects.filter(pk=lucky.pk).update(
            avg_rating=5.0, rating_count=1, bookmark_count=0
        )

        ranked, strategy = recommend_for_user(user, limit=2)
        assert strategy == "cold_start_popularity"
        assert [r.tool.name for r in ranked] == ["Strong", "Lucky"]

    def test_popularity_never_zero_for_quiet_tools(self, api_user, category, make_tool):
        user, _ = api_user
        make_tool("Quiet", category)  # no reviews, no bookmarks
        ranked, _ = recommend_for_user(user, limit=1)
        assert ranked[0].score > 0  # neutral prior keeps it representable
