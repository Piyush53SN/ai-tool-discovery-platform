from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    verbose_name = "Tool Catalog"

    def ready(self) -> None:
        # Connect tool lifecycle signals -> background embedding jobs.
        from . import signals  # noqa: F401
