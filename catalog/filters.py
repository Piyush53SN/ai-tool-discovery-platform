"""
Faceted filtering for /api/tools/ (Section 6.2).

Exposed as query params:

    ?category=writing,image        # comma-separated slugs (OR within facet)
    ?pricing=free,freemium         # comma-separated tier keys
    ?tags=code,no-code             # comma-separated tag slugs (OR + distinct)
    ?min_rating=4.5                # floor on denormalised avg_rating
    ?ordering=-avg_rating          # see ToolViewSet.ordering_fields
"""
from django_filters import rest_framework as filters

from .models import Tool


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class ToolFilter(filters.FilterSet):
    category = filters.CharFilter(method="filter_category", help_text="slug[,slug…]")
    pricing = filters.CharFilter(method="filter_pricing", help_text="tier[,tier…]")
    tags = filters.CharFilter(method="filter_tags", help_text="slug[,slug…]")
    min_rating = filters.NumberFilter(
        field_name="avg_rating", lookup_expr="gte", help_text="1–5"
    )

    class Meta:
        model = Tool
        fields = ()

    # Multi-select within a facet is OR ("give me free OR freemium"), which is
    # how faceted UIs behave; combining facets stays AND.
    def filter_category(self, queryset, name, value):
        return queryset.filter(category__slug__in=_split(value))

    def filter_pricing(self, queryset, name, value):
        tiers = [v for v in _split(value) if v in Tool.PricingTier.values]
        return queryset.filter(pricing_tier__in=tiers)

    def filter_tags(self, queryset, name, value):
        # distinct(): M2M joins can duplicate rows when a tool matches >1 tag.
        return queryset.filter(tags__slug__in=_split(value)).distinct()
