"""Kept apart from swagger.py: drf_yasg imports this at load time, so it may only import drf_yasg.inspectors.base."""

from drf_yasg import openapi
from drf_yasg.inspectors.base import PaginatorInspector


class HeaderPagedListInspector(PaginatorInspector):
    """List endpoints return a bare JSON array with paging in response headers, not a results envelope."""

    def get_paginator_parameters(self, paginator):
        return [
            openapi.Parameter('page', openapi.IN_QUERY, description='Page number', type=openapi.TYPE_INTEGER),
            openapi.Parameter('limit', openapi.IN_QUERY, description='Page size', type=openapi.TYPE_INTEGER),
        ]

    def get_paginated_response(self, paginator, response_schema):
        return response_schema
