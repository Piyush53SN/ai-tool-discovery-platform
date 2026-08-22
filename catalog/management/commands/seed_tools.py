"""
Seed the demo catalog (Section 8 of the spec: 100–300 tools).

    python manage.py seed_tools [--count 160] [--demo-user]

Steps:
  1. Categories + tags (union of the curated lists and every tag referenced
     by a real tool).
  2. ~120 curated real tools, padded with deterministic synthetic tools up to
     --count.
  3. ONE batched embedding pass (single model call for all tools — the fast
     path of the offline pipeline; the per-tool signal path stays exercised by
     admin edits and new submissions).
  4. Optional demo user with a believable interaction history so the
     recommendation endpoint returns something meaningful immediately:
        demo / demo-pass-123   (bookmarks + reviews + views on Writing &
        Productivity tools, so the taste vector points somewhere specific)
"""
from __future__ import annotations

import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Category, Tag, Tool
from catalog.tasks import batch_generate_embeddings
from catalog.management.commands.tool_data import CATEGORIES, REAL_TOOLS, TAGS
from interactions.models import Interaction, InteractionType, INTERACTION_WEIGHTS, review_weight
from interactions.services import recompute_rating_stats
from recommendations.services import recompute_preference_vector

SYNTHETIC_PREFIXES = [
    "Draftly", "Prompta", "Neuro", "Cognita", "Synthex", "Vocalia", "Pixelon",
    "Mindforge", "Clarion", "Texturly", "Insight", "Lumenor", "Cogworks",
    "Scribely", "Ideatic", "Verbalux", "Quantify", "Sketchron",
]
SYNTHETIC_SUFFIXES = ["AI", "Studio", "Pro", "Labs", "Flow", "Engine", "Kit", "X"]

SYNTHETIC_DESCRIPTIONS = {
    "Writing": [
        "Drafts {kind} in your brand voice, with tone controls and outline-first generation for content teams.",
        "Long-form writing assistant that researches a topic, structures an outline and expands sections with citations.",
        "Rewrites and tightens {kind} for clarity, keeping terminology consistent across your style guide.",
    ],
    "Image Generation": [
        "Text-to-image generator specialized in {kind} with style presets and precise composition control.",
        "Image editing suite combining inpainting, background removal and upscaling for {kind} workflows.",
        "Generates consistent characters and product shots from reference images for {kind} campaigns.",
    ],
    "Video & Audio": [
        "Produces {kind} from a script using realistic AI voices and auto-matched b-roll.",
        "Edits long-form recordings into short {kind} with captions, hooks and reframing.",
        "Voice studio cloning your timbre for multilingual {kind} with lip-synced delivery.",
    ],
    "Code & Development": [
        "Coding agent that reads your repository, plans a change and opens pull requests for {kind}.",
        "Developer copilot generating tests, docs and refactors for {kind} with static-analysis guardrails.",
        "Turns product tickets into reviewed implementations, handling {kind} migrations and CI fixes.",
    ],
    "Chatbots & Assistants": [
        "Grounded assistant answering customer questions from your docs with {kind} handoff rules.",
        "No-code agent builder deploying {kind} assistants across chat, email and voice channels.",
        "Retrieval-augmented chatbot trained on your knowledge base, with analytics on {kind} gaps.",
    ],
    "Productivity": [
        "Automates {kind} by connecting your calendar, notes and tasks into one AI-planned day.",
        "Summarizes meetings and documents into {kind} action items synced to your project tracker.",
        "Personal workspace that files every note, email and highlight automatically for instant recall.",
    ],
    "Data & Analytics": [
        "Conversational analyst turning plain-English questions into SQL, charts and {kind} reports.",
        "Forecasts {kind} from spreadsheet data with anomaly explanations in plain language.",
        "Cleans and enriches messy CSVs, detecting outliers and suggesting {kind} transformations.",
    ],
    "Marketing & SEO": [
        "Generates and tests ad variants for {kind}, scoring creative before you spend budget.",
        "SEO suite that briefs writers, tracks SERP movement and audits {kind} content health.",
        "Plans and schedules social campaigns, writing platform-native {kind} copy automatically.",
    ],
    "Design & UI": [
        "Generates editable UI screens and design systems from text prompts and {kind} references.",
        "Brand kit builder producing logos, palettes and typography tuned for {kind} positioning.",
        "Prototyping copilot wiring interactive flows from sketches for {kind} testing.",
    ],
    "Research & Education": [
        "Summarizes papers into {kind} literature tables with claim-level citations.",
        "Socratic tutor breaking {kind} concepts into adaptive practice with instant feedback.",
        "Evidence search across peer-reviewed sources answering {kind} questions with sourced quotes.",
    ],
}
KINDS = [
    "blog posts", "landing pages", "cold emails", "release notes", "product photos",
    "logo concepts", "podcast episodes", "training videos", "pull requests",
    "bug fixes", "support tickets", "onboarding flows", "standup notes",
    "quarterly reports", "dashboards", "ad creatives", "keyword clusters",
    "wireframes", "flashcards", "literature reviews", "case studies",
]

# Demo user scenario: a content-strategy persona — bookmark/review/write-heavy
# on Writing + Marketing tools, plus a couple of stray Productivity picks.
DEMO_INTEREST_SLUGS = [
    "jasper", "copy-ai", "grammarly", "quillbot", "sudowrite", "surfer-seo",
    "frase", "adcreative-ai", "notion-ai", "otter-ai",
]
DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Seed categories, tags, N tools with embeddings, and a demo user."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=160, help="Total tools to create (100–300).")
        parser.add_argument("--demo-user", action="store_true", default=True)
        parser.add_argument("--no-demo-user", dest="demo_user", action="store_false")
        parser.add_argument("--flush", action="store_true", help="Delete existing tools/categories first.")

    # ------------------------------------------------------------------
    @transaction.atomic
    def handle(self, *args, **options):
        count = max(30, min(300, options["count"]))
        rng = random.Random(42)  # deterministic runs

        if options["flush"]:
            deleted, _ = Tool.objects.all().delete()
            self.stdout.write(f"Flushed {deleted} tools.")

        categories = self._seed_categories()
        tags = self._seed_tags()

        created_tools = self._seed_real_tools(categories, tags)
        if count > len(REAL_TOOLS):
            created_tools += self._seed_synthetic_tools(
                categories, tags, rng, target=count - len(REAL_TOOLS)
            )

        # One batched pass over everything missing a fresh embedding.
        missing = list(Tool.objects.filter(embedding__isnull=True).values_list("pk", flat=True))
        if missing:
            self.stdout.write(f"Embedding {len(missing)} tools (single batched pass)…")
            ok, _ = batch_generate_embeddings(missing)
            self.stdout.write(self.style.SUCCESS(f"  embedded {ok}"))

        self.stdout.write(self.style.SUCCESS(f"Catalog ready: {Tool.objects.count()} tools."))

        if options["demo_user"]:
            self._seed_demo_user(rng)

    # ------------------------------------------------------------------
    def _seed_categories(self) -> dict[str, Category]:
        cats = {}
        for name, description in CATEGORIES:
            cat, _ = Category.objects.get_or_create(
                slug=slugify(name), defaults={"name": name, "description": description}
            )
            cats[name] = cat
        return cats

    def _seed_tags(self) -> dict[str, Tag]:
        """All curated tags + every tag referenced by a real tool entry."""
        names = set(TAGS)
        for _, _, _, tool_tags, _ in REAL_TOOLS:
            names.update(tool_tags)
        tags = {}
        for name in sorted(names):
            tag, _ = Tag.objects.get_or_create(slug=slugify(name), defaults={"name": name})
            tags[name] = tag
        return tags

    def _seed_real_tools(self, categories, tags) -> list[Tool]:
        created = []
        for name, cat, pricing, tool_tags, description in REAL_TOOLS:
            tool, was_created = Tool.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    "name": name,
                    "description": description,
                    "url": f"https://example.com/{slugify(name)}",
                    "category": categories[cat],
                    "pricing_tier": pricing,
                },
            )
            if was_created:
                tool.tags.set([tags[t] for t in tool_tags])
                created.append(tool)
        self.stdout.write(f"Real tools: {len(created)} created.")
        return created

    def _seed_synthetic_tools(self, categories, tags, rng, target) -> list[Tool]:
        existing = set(Tool.objects.values_list("slug", flat=True))
        created = []
        combos = len(SYNTHETIC_PREFIXES) * len(SYNTHETIC_SUFFIXES)
        i = 0
        while len(created) < target and i < combos * 3:
            prefix = SYNTHETIC_PREFIXES[i % len(SYNTHETIC_PREFIXES)]
            suffix = SYNTHETIC_SUFFIXES[(i // len(SYNTHETIC_PREFIXES)) % len(SYNTHETIC_SUFFIXES)]
            i += 1
            name = f"{prefix} {suffix}"
            slug = slugify(name)
            if slug in existing:
                continue
            cat_name = rng.choice(CATEGORIES)[0]
            kind = rng.choice(KINDS)
            description = rng.choice(SYNTHETIC_DESCRIPTIONS[cat_name]).format(kind=kind)
            tool = Tool.objects.create(
                name=name,
                slug=slug,
                description=description,
                url=f"https://example.com/{slug}",
                category=categories[cat_name],
                pricing_tier=rng.choices(["free", "freemium", "paid"], weights=[25, 50, 25])[0],
            )
            picks = rng.sample(sorted(tags), k=rng.randint(2, 5))
            tool.tags.set([tags[n] for n in picks])
            existing.add(slug)
            created.append(tool)
        self.stdout.write(f"Synthetic tools: {len(created)} created.")
        return created

    # ------------------------------------------------------------------
    def _seed_demo_user(self, rng):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"email": "demo@example.com"},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Demo user ready: demo / {DEMO_PASSWORD}"))

        if not created and user.interactions.exists():
            self.stdout.write("Demo user already has an interaction history — skipping.")
            return

        # Give ~60% of interested tools some ratings so avg_rating/popularity
        # have signal, then simulate the demo user's own behaviour.
        review_pool = {
            "jasper": 5, "copy-ai": 4, "grammarly": 5, "surfer-seo": 4,
            "notion-ai": 5, "frase": 3, "otter-ai": 4,
        }
        for tool in Tool.objects.all():
            n_reviews = rng.choice([0, 0, 0, 1, 2, 3])
            for _ in range(n_reviews):
                reviewer, _ = User.objects.get_or_create(username=f"critic{rng.randint(1, 25)}")
                reviewer.set_password("unimportant-123")
                reviewer.save()
                rating = rng.choices([2, 3, 4, 5], weights=[5, 20, 40, 35])[0]
                from interactions.models import Review
                Review.objects.get_or_create(
                    user=reviewer, tool=tool,
                    defaults={"rating": rating, "comment": ""},
                )
            if n_reviews:
                recompute_rating_stats(tool)

        for slug in DEMO_INTEREST_SLUGS:
            try:
                tool = Tool.objects.get(slug=slug)
            except Tool.DoesNotExist:
                continue
            Interaction.objects.create(
                user=user, tool=tool, type=InteractionType.VIEW,
                weight=INTERACTION_WEIGHTS[InteractionType.VIEW],
            )
            Interaction.objects.create(
                user=user, tool=tool, type=InteractionType.VIEW,
                weight=INTERACTION_WEIGHTS[InteractionType.VIEW],
            )
            if slug in review_pool:
                from interactions.models import Review
                rating = review_pool[slug]
                Review.objects.get_or_create(
                    user=user, tool=tool,
                    defaults={"rating": rating, "comment": "Solid part of my workflow."},
                )
                Interaction.objects.update_or_create(
                    user=user, tool=tool, type=InteractionType.REVIEW,
                    defaults={"weight": review_weight(rating)},
                )
            else:
                from interactions.models import Bookmark
                Bookmark.objects.get_or_create(user=user, tool=tool)
                Tool.objects.filter(pk=tool.pk).update(bookmark_count=tool.bookmark_count + 1)
                Interaction.objects.get_or_create(
                    user=user, tool=tool, type=InteractionType.BOOKMARK,
                    defaults={"weight": INTERACTION_WEIGHTS[InteractionType.BOOKMARK]},
                )

        recompute_preference_vector(user)
        self.stdout.write(self.style.SUCCESS(
            "Demo history seeded: 10 interacted tools -> preference vector ready."
        ))
