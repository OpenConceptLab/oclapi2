
def translate_fhir_query(fhir_query_fields, query_params, queryset):
    query_fields = fhir_query_fields.copy()
    url = query_params.get('url')
    if url:
        queryset = queryset.filter(canonical_url=url)
        query_fields.remove('url')
    language = query_params.get('language')
    if language:
        queryset = queryset.filter(default_locale=language)
        query_fields.remove('language')
    status = query_params.get('status')
    if status:
        query_fields.remove('status')
        if status == 'retired':
            queryset = queryset.filter(retired=True)
        elif status == 'active':
            queryset = queryset.filter(released=True)
        elif status == 'draft':
            queryset = queryset.filter(released=False)
    title = query_params.get('title')
    if title:
        query_fields.remove('title')
        queryset = queryset.filter(full_name=title)
    for query_field in query_fields:
        query_value = query_params.get(query_field)
        if query_value:
            kwargs = {query_field: query_value}
            queryset = queryset.filter(**kwargs)
    return queryset
