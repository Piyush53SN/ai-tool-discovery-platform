"""
Backfill / regenerate tool embeddings offline.

    python manage.py backfill_embeddings               # missing + stale only
    python manage.py backfill_embeddings --all         # regenerate everything
    python manage.py backfill_embeddings --vector-index  # + create the ivfflat
                                                       # ANN index if the catalog
                                                       # is large enough
    python manage.py backfill_embeddings --reindex     # rebuild the ANN index

Safe to run on a schedule — embeddings are the only thing written, and never
on the request path.
"""
from django.core.management.base import BaseCommand

from catalog.db import (
    INDEX_NAME,
    create_ivfflat_index,
    drop_ivfflat_index,
    ivfflat_index_exists,
)
from catalog.models import Tool
from catalog.tasks import batch_generate_embeddings


class Command(BaseCommand):
    help = "Generate embeddings for tools that are missing or stale; manage the ANN index."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-embed every tool.")
        parser.add_argument(
            "--vector-index",
            action="store_true",
            help=f"Create the {INDEX_NAME} ANN index (only if >= 500 tools, or with --force-index).",
        )
        parser.add_argument(
            "--force-index",
            action="store_true",
            help="Create the ANN index regardless of catalog size.",
        )
        parser.add_argument(
            "--reindex",
            action="store_true",
            help="Drop and recreate the ANN index (run after mass re-embeds).",
        )
        parser.add_argument("--drop-index", action="store_true", help="Remove the ANN index.")

    def handle(self, *args, **options):
        tools = Tool.objects.prefetch_related("tags")
        if options["all"]:
            targets = [t.pk for t in tools]
        else:
            targets = [t.pk for t in tools if t.embedding_is_stale]

        if not targets:
            self.stdout.write("Nothing to do — all embeddings are fresh.")
        else:
            self.stdout.write(f"Embedding {len(targets)} tool(s) in batches…")
            ok, skipped = batch_generate_embeddings(targets)
            self.stdout.write(self.style.SUCCESS(f"embedded={ok} skipped={skipped}"))

        # ---- ANN index lifecycle ------------------------------------------
        # CONCURRENTLY cannot run inside a transaction — this command runs
        # without one by design.
        if options["drop_index"] and drop_ivfflat_index():
            self.stdout.write(f"Dropped {INDEX_NAME}.")
        if options["reindex"] and ivfflat_index_exists():
            drop_ivfflat_index()
            status = create_ivfflat_index(force=True)
            self.stdout.write(f"Rebuilt ANN index: {status}.")
        if options["vector_index"]:
            status = create_ivfflat_index(force=options["force_index"])
            self.stdout.write(
                f"ANN index: {status or 'skipped — catalog smaller than the ANN threshold'} "
                f"(vector-ordered queries stay exact via seq scan)."
            )
