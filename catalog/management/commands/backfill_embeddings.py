"""
Backfill / regenerate tool embeddings offline.

    python manage.py backfill_embeddings            # missing + stale only
    python manage.py backfill_embeddings --all      # regenerate everything
    python manage.py backfill_embeddings --reindex  # rebuild the ivfflat ANN index afterwards

Used by the seeder implicitly and safe to run on a schedule — embeddings are
the only thing written, never on the request path.
"""
from django.core.management.base import BaseCommand
from django.db import connection

from catalog.models import Tool
from catalog.tasks import batch_generate_embeddings


class Command(BaseCommand):
    help = "Generate embeddings for tools that are missing or stale."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-embed every tool.")
        parser.add_argument(
            "--reindex", action="store_true",
            help="REINDEX the ivfflat embedding index afterwards (recommended after mass re-embeds).",
        )

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

        if options["reindex"]:
            with connection.cursor() as cursor:
                cursor.execute("REINDEX INDEX CONCURRENTLY tool_embedding_ivfflat;")
            self.stdout.write("Reindexed tool_embedding_ivfflat.")
