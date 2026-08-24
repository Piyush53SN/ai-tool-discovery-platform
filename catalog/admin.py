from django.contrib import admin

from .embeddings import embedding_backend_name
from .models import Category, Tag, Tool
from .tasks import dispatch_tool_embedding


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "tool_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)

    @admin.display(description="Tools")
    def tool_count(self, obj: Category) -> int:
        return obj.tools.count()


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "tool_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)

    @admin.display(description="Tools")
    def tool_count(self, obj: Tag) -> int:
        return obj.tools.count()


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "pricing_tier",
        "avg_rating",
        "rating_count",
        "bookmark_count",
        "has_embedding",
        "is_live",
        "http_status",
        "needs_review",
        "created_at",
    )
    list_filter = ("pricing_tier", "category", "is_live", "needs_review")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("tags",)
    readonly_fields = (
        "avg_rating", "rating_count", "bookmark_count", "embedding_updated_at",
        "is_live", "last_checked_at", "http_status", "checked_title",
        "consecutive_failures", "needs_review",
    )
    actions = ("regenerate_embeddings", "verify_now")

    @admin.display(boolean=True, description="Embedded")
    def has_embedding(self, obj: Tool) -> bool:
        return obj.embedding is not None

    @admin.action(description="Verify selected tools' links now")
    def verify_now(self, request, queryset):
        import httpx

        from .verification import verify_tool

        with httpx.Client(follow_redirects=True) as client:
            for tool in queryset:
                verify_tool(tool, client=client)
        self.message_user(request, f"Verified {queryset.count()} link(s).")

    @admin.action(description="Regenerate embeddings for selected tools")
    def regenerate_embeddings(self, request, queryset):
        # Dispatched off the request path like every other embedding job.
        for tool in queryset:
            dispatch_tool_embedding(tool.pk)
        self.message_user(
            request,
            f"Dispatched {queryset.count()} embedding job(s) "
            f"(backend: {embedding_backend_name()}).",
        )
