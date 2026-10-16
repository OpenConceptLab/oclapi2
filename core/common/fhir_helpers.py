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
