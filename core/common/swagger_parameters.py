from drf_yasg import openapi

from core.collections.constants import SOURCE_TO_CONCEPTS, SOURCE_MAPPINGS
from core.common.constants import RELEASED_PARAM, VERBOSE_PARAM, INCLUDE_RETIRED_PARAM, PROCESSING_PARAM, \
    INCLUDE_INVERSE_MAPPINGS_PARAM, UPDATED_SINCE_PARAM, INCLUDE_SOURCE_VERSIONS, INCLUDE_COLLECTION_VERSIONS, \
    LAST_LOGIN_BEFORE_PARAM, LAST_LOGIN_SINCE_PARAM, DATE_JOINED_SINCE_PARAM, DATE_JOINED_BEFORE_PARAM, \
    CASCADE_HIERARCHY_PARAM, CASCADE_METHOD_PARAM, MAP_TYPES_PARAM, EXCLUDE_MAP_TYPES_PARAM, CASCADE_MAPPINGS_PARAM, \
    INCLUDE_MAPPINGS_PARAM, CASCADE_LEVELS_PARAM, CASCADE_DIRECTION_PARAM, ALL, RETURN_MAP_TYPES, OMIT_IF_EXISTS_IN, \
    EQUIVALENCY_MAP_TYPES, CANONICAL_URL_REQUEST_PARAM
# HEADERS
from core.orgs.constants import NO_MEMBERS

include_facets_header = openapi.Parameter(
    'INCLUDEFACETS', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
)
search_from_latest_repo_header = openapi.Parameter(
    'INCLUDESEARCHLATEST', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
)
# HEADERS
compress_header = openapi.Parameter(
    'COMPRESS', openapi.IN_HEADER, type=openapi.TYPE_BOOLEAN, default=False
)

# QUERY PARAMS
q_param = openapi.Parameter('q', openapi.IN_QUERY, description="search text", type=openapi.TYPE_STRING)
page_param = openapi.Parameter('page', openapi.IN_QUERY, description="page number", type=openapi.TYPE_INTEGER)
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
start_date_param = openapi.Parameter(
    'start', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='YYYY-MM-DD', required=False
)
end_date_param = openapi.Parameter(
    'end', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='YYYY-MM-DD', required=False
)
include_retired_param = openapi.Parameter(
    INCLUDE_RETIRED_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
)
omit_if_exists_in_param = openapi.Parameter(
    OMIT_IF_EXISTS_IN, openapi.IN_QUERY, type=openapi.TYPE_STRING,
    description='e.g. /orgs/MyOrg/collections/MyCollection/v1/'
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
canonical_url_param = openapi.Parameter(
    CANONICAL_URL_REQUEST_PARAM, openapi.IN_QUERY, type=openapi.TYPE_STRING,
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
    'file', openapi.IN_FORM, description="JSON Content File (json, csv or zip)", type=openapi.TYPE_FILE
)
file_url_param = openapi.Parameter(
    'file_url', openapi.IN_FORM, description="Import FILE URL (json, csv or zip)", type=openapi.TYPE_STRING
)
apps_param = openapi.Parameter(
    'apps', openapi.IN_FORM, description="App Names (comma separated)", type=openapi.TYPE_STRING
)
ids_param = openapi.Parameter(
    'ids', openapi.IN_FORM, description="Resource Ids", type=openapi.TYPE_STRING
)
uri_param = openapi.Parameter(
    'uri', openapi.IN_FORM, description="Relative URI", type=openapi.TYPE_STRING
)
filter_param = openapi.Parameter(
    'filter', openapi.IN_FORM, description="Generic Filter", type=openapi.TYPE_OBJECT
)
resources_body_param = openapi.Parameter(
    'resource', openapi.IN_PATH, type=openapi.TYPE_STRING,
    enum=['mappings', 'concepts', 'sources', 'orgs', 'users', 'collections']
)
all_resource_query_param = openapi.Parameter(
    'resource',
    openapi.IN_QUERY,
    description="Resource type to generate checksum",
    type=openapi.TYPE_STRING,
    default='concept_version',
    enum=['concept_version', 'mapping_version', 'source_version', 'collection_version', 'org', 'user'],
    required=True
)
parallel_threads_param = openapi.Parameter(
    'parallel', openapi.IN_FORM, description="Parallel threads count (default: 5, max: 10)", type=openapi.TYPE_INTEGER
)
org_no_members_param = openapi.Parameter(
    NO_MEMBERS, openapi.IN_QUERY, description="Get all orgs without any members", type=openapi.TYPE_BOOLEAN,
    default=False
)

# cascade params
cascade_method_param = openapi.Parameter(
    CASCADE_METHOD_PARAM, openapi.IN_QUERY, type=openapi.TYPE_STRING,
    enum=[SOURCE_TO_CONCEPTS, SOURCE_MAPPINGS], default=SOURCE_TO_CONCEPTS
)
cascade_map_types_param = openapi.Parameter(
    MAP_TYPES_PARAM, openapi.IN_QUERY, type=openapi.TYPE_ARRAY,
    items=openapi.Items(type=openapi.TYPE_STRING),
    uniqueItems=True
)
cascade_exclude_map_types_param = openapi.Parameter(
    EXCLUDE_MAP_TYPES_PARAM, openapi.IN_QUERY, type=openapi.TYPE_ARRAY,
    items=openapi.Items(type=openapi.TYPE_STRING),
    uniqueItems=True
)
equivalency_map_types_param = openapi.Parameter(
    EQUIVALENCY_MAP_TYPES, openapi.IN_QUERY, type=openapi.TYPE_ARRAY,
    items=openapi.Items(type=openapi.TYPE_STRING),
    uniqueItems=True
)
return_map_types_param = openapi.Parameter(
    RETURN_MAP_TYPES, openapi.IN_QUERY, type=openapi.TYPE_ARRAY,
    items=openapi.Items(type=openapi.TYPE_STRING),
    uniqueItems=True
)
cascade_hierarchy_param = openapi.Parameter(
    CASCADE_HIERARCHY_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=True
)
cascade_mappings_param = openapi.Parameter(
    CASCADE_MAPPINGS_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=True
)
include_mappings_param = openapi.Parameter(
    INCLUDE_MAPPINGS_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=True
)
cascade_levels_param = openapi.Parameter(
    CASCADE_LEVELS_PARAM, openapi.IN_QUERY, type=openapi.TYPE_STRING, default=ALL, description=f'0, 1, 2...{ALL}'
)
cascade_direction_param = openapi.Parameter(
    CASCADE_DIRECTION_PARAM, openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False,
    description='$cascade backward or up'
)
cascade_view_hierarchy = openapi.Parameter(
    'view', openapi.IN_QUERY, type=openapi.TYPE_STRING, default='',
    enum=['', 'hierarchy'],
    description='Hierarchy (nested) or Flat Response'
)
