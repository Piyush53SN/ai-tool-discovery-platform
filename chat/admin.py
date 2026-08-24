from django.contrib import admin

from .models import ChatSession, ChatTurn, ModelResponse


class ModelResponseInline(admin.TabularInline):
    model = ModelResponse
    extra = 0
    readonly_fields = ("model_id", "provider", "response_text", "token_count",
                       "latency_ms", "status", "error_message")


class ChatTurnInline(admin.TabularInline):
    model = ChatTurn
    extra = 0
    readonly_fields = ("id", "prompt", "created_at")
    show_change_link = True


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "created_at", "turn_count")
    inlines = (ChatTurnInline,)

    @admin.display(description="Turns")
    def turn_count(self, obj):
        return obj.turns.count()


@admin.register(ChatTurn)
class ChatTurnAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "prompt", "created_at")
    inlines = (ModelResponseInline,)


@admin.register(ModelResponse)
class ModelResponseAdmin(admin.ModelAdmin):
    list_display = ("model_id", "provider", "status", "token_count", "latency_ms", "turn")
    list_filter = ("provider", "status")
    search_fields = ("model_id", "response_text")
