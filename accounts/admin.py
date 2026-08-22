from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "has_preference_vector", "preference_updated_at", "onboarding_tag_names")
    search_fields = ("user__username",)

    @admin.display(boolean=True, description="Pref vector")
    def has_preference_vector(self, obj: Profile) -> bool:
        return obj.preference_vector is not None

    @admin.display(description="Onboarding tags")
    def onboarding_tag_names(self, obj: Profile) -> str:
        return ", ".join(t.name for t in obj.onboarding_tags.all()) or "—"
