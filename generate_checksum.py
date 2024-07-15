import argparse
import hashlib
import json
from uuid import UUID
from pprint import pprint


def generic_sort(_list):
    def compare(item):
        if isinstance(item, (int, float, str, bool)):
            return item
        return str(item)
    return sorted(_list, key=compare)


def _serialize(obj):
    if isinstance(obj, list) and len(obj) == 1:
        obj = obj[0]
    if isinstance(obj, list):
        return f"[{','.join(map(_serialize, generic_sort(obj)))}]"
    if isinstance(obj, dict):
        keys = generic_sort(obj.keys())
        acc = f"{{{json.dumps(keys)}"
        for key in keys:
            acc += f"{_serialize(obj[key])},"
        return f"{acc}}}"
    if isinstance(obj, UUID):
        return json.dumps(str(obj))
    return json.dumps(obj)


def _cleanup(fields):
    result = fields
    if isinstance(fields, dict):  # pylint: disable=too-many-nested-blocks
        result = {}
        for key, value in fields.items():
            if value is None:
                continue
            if key in [
                'retired', 'parent_concept_urls', 'child_concept_urls', 'descriptions', 'extras', 'names'
            ] and not value:
                continue
            if key in ['is_active'] and value:
                continue
            if isinstance(value, (int, float)):
                if int(value) == float(value):
                    value = int(value)
            if key in ['extras']:
                if isinstance(value, dict) and any(key.startswith('__') for key in value):
                    value_copied = value.copy()
                    for extra_key in value:
                        if extra_key.startswith('__'):
                            value_copied.pop(extra_key)
                    value = value_copied
            result[key] = value
    return result


def _locales_for_checksums(data, relation, fields, predicate_func):
    locales = data.get(relation, [])
    return [{field: locale.get(field, None) for field in fields} for locale in locales if predicate_func(locale)]


def _generate(obj, hash_algorithm='MD5'):
    # hex encoding is used to make the hash more readable
    serialized_obj = _serialize(obj).encode('utf-8')
    print("\n")
    print("After Serialization")
    print(serialized_obj.decode())
    hash_func = hashlib.new(hash_algorithm)
    hash_func.update(serialized_obj)

    return hash_func.hexdigest()


def is_fully_specified_type(_type):
    if not _type:
        return False
    if _type in ('FULLY_SPECIFIED', "Fully Specified"):
        return True
    _type = _type.replace(' ', '').replace('-', '').replace('_', '').lower()
    return _type == 'fullyspecified'


def get_concept_fields(data, checksum_type):
    name_fields = ['locale', 'locale_preferred', 'name', 'name_type', 'external_id']
    description_fields = ['locale', 'locale_preferred', 'description', 'description_type', 'external_id']
    if checksum_type == 'standard':
        return {
            'concept_class': data.get('concept_class', None),
            'datatype': data.get('datatype', None),
            'retired': data.get('retired', False),
            'external_id': data.get('external_id', None),
            'extras': data.get('extras', None),
            'names': _locales_for_checksums(
                data,
                'names',
                name_fields,
                lambda _: True
            ),
            'descriptions': _locales_for_checksums(
                data,
                'descriptions',
                description_fields,
                lambda _: True
            ),
            'parent_concept_urls': data.get('parent_concept_urls', []),
            'child_concept_urls': data.get('child_concept_urls', []),
        }
    return {
            'concept_class': data.get('concept_class', None),
            'datatype': data.get('datatype', None),
            'retired': data.get('retired', False),
            'names': _locales_for_checksums(
                data,
                'names',
                name_fields,
                lambda locale: is_fully_specified_type(locale.get('name_type', None))
            ),
        }


def get_mapping_fields(data, checksum_type):
    fields = {
            'map_type': data.get('map_type', None),
            'from_concept_code': data.get('from_concept_code', None),
            'to_concept_code': data.get('to_concept_code', None),
            'from_concept_name': data.get('from_concept_name', None),
            'to_concept_name': data.get('to_concept_name', None),
            'retired': data.get('retired', False)
        }
    if checksum_type == 'standard':
        return {
            **fields,
            'sort_weight': float(data.get('sort_weight', 0)) or None,
            **{
                field: data.get(field, None) or None for field in [
                    'extras',
                    'external_id',
                    'from_source_url',
                    'from_source_version',
                    'to_source_url',
                    'to_source_version'
                ]
            }
        }
    return fields


def flatten(input_list, depth=1):
    result = []
    for item in input_list:
        if isinstance(item, list) and depth > 0:
            result.extend(flatten(item, depth - 1))
        else:
            result.append(item)
    return result


def generate(resource, data, checksum_type='standard'):
    if not resource or resource.lower() not in ['concept', 'mapping']:
        raise ValueError(f"Invalid resource: {resource}")
    if checksum_type not in ['standard', 'smart']:
        raise ValueError(f"Invalid checksum type: {checksum_type}")

    if resource == 'concept':
        data = [get_concept_fields(_data, checksum_type) for _data in flatten([data])]
    elif resource == 'mapping':
        data = [get_mapping_fields(_data, checksum_type) for _data in flatten([data])]
    print("\n")
    print("Fields for Checksum:")
    pprint(data)

    print("\n")
    print("After Cleanup:")
    pprint([_cleanup(_data) for _data in data])

    checksums = [
        _generate(_cleanup(_data)) for _data in data
    ] if isinstance(data, list) else [
        _generate(_cleanup(data))
    ]
    if len(checksums) == 1:
        return checksums[0]
    return _generate(checksums)


def main():
    parser = argparse.ArgumentParser(description='Generate checksum for resource data.')
    parser.add_argument(
        '-r', '--resource', type=str, choices=['concept', 'mapping'], help='The type of resource (concept, mapping)')
    parser.add_argument(
        '-c', '--checksum_type', type=str, default='standard', choices=['standard', 'smart'],
        help='The type of checksum to generate (default: standard)')
    parser.add_argument(
        '-d', '--data', type=str, help='The data for which checksum needs to be generated')

    args = parser.parse_args()


    try:
        result = generate(args.resource, json.loads(args.data), args.checksum_type)
        print("\n")
        print('\x1b[6;30;42m' + f'{args.checksum_type.title()} Checksum: {result}' + '\x1b[0m')
        print("\n")
    except Exception as e:
        print(e)
        print()
        usage()


def usage() -> None:
    print("Use this as:")
    print("python3 core/generate_checksum.py <concept|mapping> '{...json...}' <standard|smart>")


if __name__ == '__main__':
    main()
