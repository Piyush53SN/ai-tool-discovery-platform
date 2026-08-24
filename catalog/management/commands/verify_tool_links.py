"""
Run the link-verification crawler manually (same core as the Celery beat task).

    python manage.py verify_tool_links [--limit 50] [--slug some-tool] [--no-jitter]

The Celery schedule runs this hourly-ish in production; the command exists for
manual runs, cron setups without a worker, and smoke-testing.
"""
from django.core.management.base import BaseCommand

from catalog.verification import DEFAULT_JITTER, verify_due_tools


class Command(BaseCommand):
    help = "Verify a bounded batch of due tool links (robots-aware, rate-limited)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--slug", default=None, help="verify a single tool")
        parser.add_argument(
            "--no-jitter", action="store_true",
            help="skip the polite sleep between requests (for tests/local runs)",
        )

    def handle(self, *args, **options):
        jitter = (0.0, 0.0) if options["no_jitter"] else DEFAULT_JITTER
        summary = verify_due_tools(
            limit=options["limit"], jitter=jitter, only_slug=options["slug"]
        )
        self.stdout.write(self.style.SUCCESS(f"verification run: {summary}"))
        flagged = self.style.WARNING(
            "tools flagged needs_review: run "
            "`python manage.py shell -c \"from catalog.models import Tool; "
            "print(list(Tool.objects.filter(needs_review=True).values_list('slug', flat=True)))\""
        )
        self.stdout.write(flagged)
