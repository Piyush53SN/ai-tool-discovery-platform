"""
Catalog data model: Category, Tag, Tool.

`Tool.embedding` is a pgvector column (384-dim, matching
sentence-transformers/all-MiniLM-L6-v2). Embeddings are generated OFF the
request path (management command / background worker — see catalog/embeddings.py
and catalog/tasks.py) and therefore the column is nullable: a tool is fully
usable the moment it is created, and becomes semantically searchable as soon as
its embedding lands.

Indexes:
  * btree on category / pricing_tier  -> faceted filtering
  * ivfflat (cosine) on embedding     -> approximate nearest-neighbour search,
    for when the catalog is large enough to justify it. It is NOT part of the
    initial migration on purpose: with few hundred rows Postgres seq-scans
    (exact, fast), while an ivfflat index with default probes=1 would DROP
    results (k-means lists on a tiny table -> rows land in lists the probe
    never visits). `manage.py backfill_embeddings --vector-index` creates it
    once >= MIN_ROWS_FOR_IVFFLAT tools exist (lists ~= rows/1000), and
    catalog/db.py raises ivfflat.probes for vector-ordered queries.
"""
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from pgvector.django import VectorField

EMBEDDING_DIM = settings.EMBEDDING_DIM


class Category(models.Model):
    """Top-level functional grouping, e.g. 'Writing', 'Image Generation'."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = [Lower("name")]

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    """Free-form capability labels used for filtering and cold-start onboarding."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        ordering = [Lower("name")]

    def __str__(self) -> str:
        return self.name


class Tool(models.Model):
    """An AI tool in the catalog."""

    class PricingTier(models.TextChoices):
        FREE = "free", "Free"
        FREEMIUM = "freemium", "Freemium"
        PAID = "paid", "Paid"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField()
    url = models.URLField(max_length=500)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="tools"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="tools")
    pricing_tier = models.CharField(
        max_length=12, choices=PricingTier.choices, default=PricingTier.FREEMIUM
    )

    # ---- denormalised aggregates ------------------------------------------------
    # Both are maintained transactionally by interactions.services so list /
    # recommendation endpoints never need a GROUP BY over reviews/bookmarks.
    avg_rating = models.DecimalField(
        max_digits=3, decimal_places=2, default=0
    )  # 0.00 – 5.00
    rating_count = models.PositiveIntegerField(default=0)
    bookmark_count = models.PositiveIntegerField(default=0)

    # ---- semantic layer ----------------------------------------------------------
    embedding = VectorField(
        dimensions=EMBEDDING_DIM,
        null=True,
        help_text="384-dim sentence embedding of name + description + tags "
        "(generated offline, never on the request path).",
    )
    embedding_updated_at = models.DateTimeField(null=True, blank=True)

    # ---- link verification (catalog/verification.py, Section 3.2) -----------
    is_live = models.BooleanField(
        default=True, help_text="Last known state of the tool's public URL."
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    checked_title = models.CharField(
        max_length=500, null=True, blank=True,
        help_text="<title> of the target page at last check (staleness signal).",
    )
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    needs_review = models.BooleanField(
        default=False,
        help_text="Set after 3+ consecutive failed checks — a human decides, "
        "never auto-delete (crawl errors are often transient outages).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-avg_rating", "-created_at")
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["pricing_tier"]),
            # NOTE: the ivfflat ANN index on `embedding` is intentionally NOT
            # here — see the module docstring and catalog/db.py.
        ]

    def __str__(self) -> str:
        return self.name

    # --------------------------------------------------------------------------
    @property
    def embedding_is_stale(self) -> bool:
        """True when the row changed after its embedding was last computed."""
        return self.embedding_updated_at is None or self.updated_at > self.embedding_updated_at

    def embedding_input(self) -> str:
        """Canonical text a tool is embedded from (Section 6.5.1 of the spec).

        Keeping this in ONE place means the seeder, backfill command and the
        signal-driven worker all produce identical vectors.
        """
        tag_names = ", ".join(t.name for t in self.tags.all()) or "none"
        return f"{self.name}. {self.description} Tags: {tag_names}"
