from django.contrib import admin

from .models import Bookmark, Interaction, Review


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ("user", "tool", "created_at")
    search_fields = ("user__username", "tool__name")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "tool", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("user__username", "tool__name", "comment")


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ("user", "tool", "type", "weight", "created_at")
    list_filter = ("type",)
    search_fields = ("user__username", "tool__name")
