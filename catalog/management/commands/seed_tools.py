"""
Seed the catalog from the curated REAL dataset (Section 3.1).

    python manage.py seed_tools [--flush] [--no-demo-user]

Data lives in `real_tools_seed.json` — real tools, real URLs, hand-written
descriptions. The file is meant to be periodically hand-EXPANDED (it is the
source of truth, not a generated artifact). The synthetic generator from the
earlier iteration was removed: fictional tools have no place in a catalog whose
links get verified by a crawler.

The command is idempotent: existing tools (matched by slug) get their curated
fields refreshed; embeddings are batch-generated for anything missing.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Category, Tag, Tool
from catalog.tasks import batch_generate_embeddings
from interactions.models import (
    INTERACTION_WEIGHTS,
    Bookmark,
    Interaction,
    InteractionType,
    Review,
    review_weight,
)
from interactions.services import recompute_rating_stats
from recommendations.services import recompute_preference_vector

DATA_FILE = Path(__file__).parent / "real_tools_seed.json"

# Demo user scenario: a content-strategy persona — bookmark/review/write-heavy
# on Writing + Marketing tools, plus a couple of Productivity picks.
DEMO_INTEREST_SLUGS = [
    "jasper", "copy-ai", "grammarly", "quillbot", "sudowrite", "surfer-seo",
    "frase", "adcreative-ai", "notion-ai", "otter-ai",
]
DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Seed categories, tags and the curated real tool catalog + demo user."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete existing tools first.")
        parser.add_argument("--demo-user", action="store_true", default=True)
        parser.add_argument("--no-demo-user", dest="demo_user", action="store_false")

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(42)  # deterministic demo noise

        if options["flush"]:
            deleted, _ = Tool.objects.all().delete()
            self.stdout.write(f"Flushed {deleted} tools.")

        entries = json.loads(DATA_FILE.read_text())
        created = self._seed_catalog(entries)

        missing = list(Tool.objects.filter(embedding__isnull=True).values_list("pk", flat=True))
        if missing:
            self.stdout.write(f"Embedding {len(missing)} tools (single batched pass)…")
            ok, _ = batch_generate_embeddings(missing)
            self.stdout.write(self.style.SUCCESS(f"  embedded {ok}"))

        self.stdout.write(self.style.SUCCESS(
            f"Catalog ready: {Tool.objects.count()} tools ({created} newly created)."
        ))

        if options["demo_user"]:
            self._seed_demo_user(rng)

    # ------------------------------------------------------------------
    def _seed_catalog(self, entries: list[dict]) -> int:
        categories: dict[str, Category] = {}
        tags: dict[str, Tag] = {}

        created = 0
        for entry in entries:
            cat_name = entry["category"]
            if cat_name not in categories:
                categories[cat_name] = Category.objects.get_or_create(
                    slug=slugify(cat_name),
                    defaults={"name": cat_name},
                )[0]
            cat = categories[cat_name]

            tool, was_created = Tool.objects.get_or_create(
                slug=slugify(entry["name"]),
                defaults={
                    "name": entry["name"],
                    "description": entry["description"],
                    "url": entry["url"],
                    "category": cat,
                    "pricing_tier": entry["pricing_tier"],
                    "is_live": True,  # assumed live at curation; crawler verifies
                },
            )
            if was_created:
                created += 1
            else:
                # Refresh curated fields on re-runs (keep ratings/bookmarks).
                tool.name = entry["name"]
                tool.description = entry["description"]
                tool.url = entry["url"]
                tool.category = cat
                tool.pricing_tier = entry["pricing_tier"]
                tool.save(update_fields=[
                    "name", "description", "url", "category", "pricing_tier", "updated_at",
                ])

            tag_objs = []
            for tag_name in entry.get("tags", []):
                if tag_name not in tags:
                    tags[tag_name] = Tag.objects.get_or_create(
                        slug=slugify(tag_name), defaults={"name": tag_name}
                    )[0]
                tag_objs.append(tags[tag_name])
            tool.tags.set(tag_objs)
        self.stdout.write(f"Real tools: {len(entries)} processed, {created} created.")
        return created

    # ------------------------------------------------------------------
    def _seed_demo_user(self, rng):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="demo", defaults={"email": "demo@example.com"}
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Demo user ready: demo / {DEMO_PASSWORD}"))

        if not created and user.interactions.exists():
            self.stdout.write("Demo user already has an interaction history — skipping.")
            return

        # Background ratings so avg_rating/popularity have signal.
        for tool in Tool.objects.all():
            n_reviews = rng.choice([0, 0, 0, 1, 2, 3])
            for _ in range(n_reviews):
                reviewer, _ = User.objects.get_or_create(username=f"critic{rng.randint(1, 25)}")
                reviewer.set_password("unimportant-123")
                reviewer.save()
                rating = rng.choices([2, 3, 4, 5], weights=[5, 20, 40, 35])[0]
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
            Interaction.objects.get_or_create(
                user=user, tool=tool, type=InteractionType.VIEW,
                defaults={"weight": INTERACTION_WEIGHTS[InteractionType.VIEW]},
            )
            Interaction.objects.get_or_create(
                user=user, tool=tool, type=InteractionType.VIEW,
                defaults={"weight": INTERACTION_WEIGHTS[InteractionType.VIEW]},
            )
            reviewed = {"jasper": 5, "copy-ai": 4, "grammarly": 5, "surfer-seo": 4,
                        "notion-ai": 5, "frase": 3, "otter-ai": 4}.get(slug)
            if reviewed:
                Review.objects.get_or_create(
                    user=user, tool=tool,
                    defaults={"rating": reviewed, "comment": "Solid part of my workflow."},
                )
                Interaction.objects.update_or_create(
                    user=user, tool=tool, type=InteractionType.REVIEW,
                    defaults={"weight": review_weight(reviewed)},
                )
            else:
                Bookmark.objects.get_or_create(user=user, tool=tool)
                Tool.objects.filter(pk=tool.pk).update(
                    bookmark_count=tool.bookmark_count + 1
                )
                Interaction.objects.get_or_create(
                    user=user, tool=tool, type=InteractionType.BOOKMARK,
                    defaults={"weight": INTERACTION_WEIGHTS[InteractionType.BOOKMARK]},
                )

        recompute_preference_vector(user)
        self.stdout.write(self.style.SUCCESS(
            "Demo history seeded: 10 interacted tools -> preference vector ready."
        ))
