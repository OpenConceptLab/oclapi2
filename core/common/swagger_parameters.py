from drf_yasg import openapi

from core.common.constants import RELEASED_PARAM, VERBOSE_PARAM, INCLUDE_RETIRED_PARAM, PROCESSING_PARAM, \
    INCLUDE_INVERSE_MAPPINGS_PARAM, UPDATED_SINCE_PARAM

# HEADERS
include_facets_header = openapi.Parameter(
    'INCLUDEFACETS', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
)

# QUERY PARAMS
q_param = openapi.Parameter('q', openapi.IN_QUERY, description="search text", type=openapi.TYPE_STRING)
page_param = openapi.Parameter('page', openapi.IN_QUERY, description="page number", type=openapi.TYPE_INTEGER)
exact_match_param = openapi.Parameter(
    'exact_match', openapi.IN_QUERY, description="on | off (no wildcards)", type=openapi.TYPE_STRING, default='off'
)
limit_param = openapi.Parameter(
    'limit', openapi.IN_QUERY, description="result list size", type=openapi.TYPE_INTEGER, default=25
)
sort_desc_param = openapi.Parameter(
    'sortDesc', openapi.IN_QUERY, description="With q param (last_update, name, best_match)", type=openapi.TYPE_STRING,
)
sort_asc_param = openapi.Parameter(
    'sortAsc', openapi.IN_QUERY, description="With q param (last_update, name, best_match)", type=openapi.TYPE_STRING,
)
verbose_param = openapi.Parameter(
    VERBOSE_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
include_retired_param = openapi.Parameter(
    INCLUDE_RETIRED_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
include_inverse_mappings_param = openapi.Parameter(
    INCLUDE_INVERSE_MAPPINGS_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
updated_since_param = openapi.Parameter(
    UPDATED_SINCE_PARAM, openapi.IN_QUERY, description="format: YYYY-MM-DD HH:MM:SS", type=openapi.TYPE_STRING,
)

released_param = openapi.Parameter(
    RELEASED_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
processing_param = openapi.Parameter(
    PROCESSING_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
