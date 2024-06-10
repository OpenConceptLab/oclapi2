import json

from core.common.exceptions import Http400


def translate_fhir_query(fhir_query_fields, query_params, queryset):
    remaining_query_params = query_params.copy()
    query_fields = fhir_query_fields.copy()
    url = remaining_query_params.get('url')
    if url and 'url' in query_fields:
        queryset = queryset.filter(canonical_url=url)
        remaining_query_params.pop('url')
        query_fields.remove('url')
    language = remaining_query_params.get('language')
    if language and 'language' in query_fields:
        queryset = queryset.filter(default_locale=language)
        remaining_query_params.pop('language')
        query_fields.remove('language')
    status = remaining_query_params.get('status')
    if status and 'status' in query_fields:
        query_fields.remove('status')
        remaining_query_params.pop('status')
        if status == 'retired':
            queryset = queryset.filter(retired=True)
        elif status == 'active':
            queryset = queryset.filter(released=True)
        elif status == 'draft':
            queryset = queryset.filter(released=False)
    title = remaining_query_params.get('title')
    if title and 'title' in query_fields:
        remaining_query_params.pop('title')
        query_fields.remove('title')
        queryset = queryset.filter(full_name=title)
    code = remaining_query_params.get('code')
    if code and 'code' in query_fields:
        remaining_query_params.pop('code')
        query_fields.remove('code')
        queryset = queryset.filter(concepts__mnemonic=code)
    for query_field in query_fields:
        query_value = remaining_query_params.get(query_field)
        if query_value:
            remaining_query_params.pop(query_field, None)
            kwargs = {query_field: query_value}
            queryset = queryset.filter(**kwargs)

    if remaining_query_params:
        raise Http400('The following query params are not supported: ' + json.dumps(remaining_query_params))

    return queryset


def delete_empty_fields(obj):
    if isinstance(obj, dict):
        for field in list(obj.keys()):
            if obj[field] is None or obj[field] == {} or obj[field] == []:
                del obj[field]
            elif isinstance(obj[field], (dict, list)):
                delete_empty_fields(obj[field])
    if isinstance(obj, list):
        for item in obj:
            delete_empty_fields(item)
