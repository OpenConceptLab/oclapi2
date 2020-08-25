from drf_yasg import openapi

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
    'verbose', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
