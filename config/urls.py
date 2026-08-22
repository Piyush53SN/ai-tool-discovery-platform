"""Root URL configuration. The DRF API lives under /api/."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def root_index(request):
    """Self-describing index at the bare root.

    This backend is API-only; the SPA runs on the Vite dev server (:5173 in
    development, proxied to /api). Hitting `/` on the API port used to be a
    bare 404 — this real route makes the wrong door explain itself instead.
    """
    return JsonResponse(
        {
            "name": "AI Tool Discovery & Recommendation Platform API",
            "api": "/api/",
            "admin": "/admin/",
            "frontend": "http://localhost:5173/ (Vite dev server in development)",
            "docs": "see README.md — full endpoint table",
        }
    )


urlpatterns = [
    path("", root_index, name="root-index"),
    path("admin/", admin.site.urls),
    path("api/", include("config.api_urls")),
]
