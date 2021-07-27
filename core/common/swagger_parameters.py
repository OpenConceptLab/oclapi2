from drf_yasg import openapi

from core.common.constants import RELEASED_PARAM, VERBOSE_PARAM, INCLUDE_RETIRED_PARAM, PROCESSING_PARAM, \
    INCLUDE_INVERSE_MAPPINGS_PARAM, UPDATED_SINCE_PARAM, INCLUDE_SOURCE_VERSIONS, INCLUDE_COLLECTION_VERSIONS, \
    LAST_LOGIN_BEFORE_PARAM, LAST_LOGIN_SINCE_PARAM, DATE_JOINED_SINCE_PARAM, DATE_JOINED_BEFORE_PARAM

# HEADERS
include_facets_header = openapi.Parameter(
    'INCLUDEFACETS', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
)
# HEADERS
compress_header = openapi.Parameter(
    'COMPRESS', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
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
include_source_versions_param = openapi.Parameter(
    INCLUDE_SOURCE_VERSIONS, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
include_collection_versions_param = openapi.Parameter(
    INCLUDE_COLLECTION_VERSIONS, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
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

last_login_before_param = openapi.Parameter(
    LAST_LOGIN_BEFORE_PARAM, openapi.IN_QUERY, description='YYYY-MM-DD (optional)', type=openapi.TYPE_STRING)

last_login_since_param = openapi.Parameter(
    LAST_LOGIN_SINCE_PARAM, openapi.IN_QUERY, description='YYYY-MM-DD (optional)', type=openapi.TYPE_STRING)

date_joined_before_param = openapi.Parameter(
    DATE_JOINED_BEFORE_PARAM, openapi.IN_QUERY, description='YYYY-MM-DD (optional)', type=openapi.TYPE_STRING)

date_joined_since_param = openapi.Parameter(
    DATE_JOINED_SINCE_PARAM, openapi.IN_QUERY, description='YYYY-MM-DD (optional)', type=openapi.TYPE_STRING)

# bulk import params
task_param = openapi.Parameter(
    'task', openapi.IN_QUERY, description="task uuid (mandatory)", type=openapi.TYPE_STRING
)
username_param = openapi.Parameter(
    'username', openapi.IN_QUERY, description="username", type=openapi.TYPE_STRING
)
result_param = openapi.Parameter(
    'result', openapi.IN_QUERY, description="result format (json | report) (optional)", type=openapi.TYPE_STRING
)
update_if_exists_param = openapi.Parameter(
    'update_if_exists', openapi.IN_QUERY, description="true | false (mandatory)", type=openapi.TYPE_STRING,
    default='true'
)
file_upload_param = openapi.Parameter(
    'file', openapi.IN_FORM, description="JSON Content File (mandatory)", type=openapi.TYPE_FILE
)
file_url_param = openapi.Parameter(
    'file_url', openapi.IN_FORM, description="Import FILE URL (mandatory)", type=openapi.TYPE_STRING
)
apps_param = openapi.Parameter(
    'apps', openapi.IN_FORM, description="App Names (comma separated)", type=openapi.TYPE_STRING
)
ids_param = openapi.Parameter(
    'ids', openapi.IN_FORM, description="Resource Ids", type=openapi.TYPE_STRING
)
resources_body_param = openapi.Parameter(
    'resource', openapi.IN_PATH, type=openapi.TYPE_STRING,
    enum=['mappings', 'concepts', 'sources', 'orgs', 'users', 'collections']
)
parallel_threads_param = openapi.Parameter(
    'parallel', openapi.IN_FORM, description="Parallel threads count (default: 5, max: 10)", type=openapi.TYPE_INTEGER
)
