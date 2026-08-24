"""Shared pagination: every list endpoint is paginated (NFR: performance)."""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class DefaultPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = "page_size"
    max_page_size = 48

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "pages": self.page.paginator.num_pages,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )
