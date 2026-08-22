"""Create a Profile for every new user (preference vector home)."""
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="auth.User")
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        from .models import Profile

        Profile.objects.get_or_create(user=instance)
