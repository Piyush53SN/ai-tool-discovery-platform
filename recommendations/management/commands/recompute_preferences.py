"""
Recompute every user's cached preference vector.

    python manage.py recompute_preferences

Also wired as a periodic Celery beat task (settings.CELERY_BEAT_SCHEDULE) —
the nightly safety net so vectors stay fresh even if a worker hiccups;
in-request laziness (recommendations.services.get_preference_vector) covers
the gaps in between.
"""
from django.core.management.base import BaseCommand

from recommendations.tasks import run_for_all_users


class Command(BaseCommand):
    help = "Refresh Profile.preference_vector for all users."

    def handle(self, *args, **options):
        count = run_for_all_users()
        self.stdout.write(self.style.SUCCESS(f"Recomputed preference vectors for {count} user(s)."))
