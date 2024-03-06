import argparse
import json
import logging
import os
import pathlib
import time
import urllib
import jsondiff

import requests
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, ALL_COMPLETED

from jsonpath_ng import parse


def response_summary(response):
    summary = ''
    if response is not None:
        summary = f'[{response.status_code}] '
        if response.content is not None:
            content = str(response.content)
            summary += (content[:2048] + '..') if len(content) > 2048 else content
    return summary


def delete(target, secret):
    logging.info(f'Deleting: {target}')

    try:
        response = None

        response = requests.delete(
            target,
            headers={'Authorization': f'Token {secret}'})

        if response.status_code == 202 or response.status_code == 409:
            logging.info(f'Pending deletion: {target}')
            logging.debug(f'DELETE {target} {response_summary(response)}')
            return {'deleted': target}
        if response.status_code != 204:
            error = f'Failed to delete {target}. {response_summary(response)}'
            logging.error(error)
            return {'failed': True, 'reason': error}
    except Exception as e:
        error = f'Failed to delete {target}. {str(e)} {response_summary(response)}'
        logging.exception(error)
        return {'failed': True, 'reason': error}

    logging.info(f'Deleted: {target}')
    return {'deleted': target}


def get(target, secret):
    headers = {'Authorization': f'Token {secret}'}
    response = requests.get(target, headers=headers, timeout=120)
    logging.debug(f'GET {target} {response_summary(response)}')
    return response


def post(target, secret, json):
    headers = {'Authorization': f'Token {secret}'}
    return requests.post(target, json=json, headers=headers, timeout=120)


def update_json(json_input, path, value):
    jsonpath_expr = parse(path)
    jsonpath_expr.update_or_create(json_input, value)


def adjust_to_ocl_format(json_file):
    if json_file.get('resourceType', None) == 'CodeSystem':
        concepts = json_file.get('concept', [])
        for concept in concepts:
            code = concept.get('code', None)
            if code:
                code = urllib.parse.quote(code, safe=' ')
                concept.update({'code': code})
        json_file.update({'concept': concepts})

        update_json(json_file, 'concept[*].property', [
            {'code': 'conceptclass', 'value': 'Misc'},
            {'code': 'datatype', 'value': 'N/A'}
        ])
        update_json(json_file, 'language', 'en')
        update_json(json_file, 'property', [
            {
                'code': 'conceptclass',
                'description': 'Standard list of concept classes.',
                'type': 'string',
            }, {
                'code': 'datatype',
                'description': 'Standard list of concept datatypes.',
                'type': 'string',
            }, {
                'code': 'inactive',
                'description': 'True if the concept is not considered active.',
                'type': 'coding',
                'uri': 'http://hl7.org/fhir/concept-properties'
            }
        ])


def ignore_json_paths(json_input, json_response, paths):
    for path in paths:
        update_json(json_input, path, None)
        update_json(json_response, path, None)

def ignore_json_paths_if_not_in_input(json_input, json_response, paths):
    for path in paths:
        jsonpath_expr = parse(path)
        if not jsonpath_expr.find(json_input):
            update_json(json_input, path, None)
            update_json(json_response, path, None)


def validate_jsons(json_file, json_response):
    adjust_to_ocl_format(json_file)

    ignore_json_paths(json_file, json_response, ['date', 'identifier', 'meta',
                                                 'revisionDate'])

    ignore_json_paths_if_not_in_input(json_file, json_response, ['count', 'concept[*].definition', 'concept[*].id',
                                                                 'concept[*].designation', 'concept[*].display',
                                                                 'property[*].uri'])

    diff = jsondiff.diff(json_file, json_response, syntax='explicit', marshal=True)
    if diff:
        diff = json.dumps(diff, indent=4)
    return diff


def create(file, target, secret, resources=[], validate=False):
    try:
        response = None
        file_size = os.path.getsize(file) / (1024 * 1024)
        if file_size > 50:
            return {"file": file, "failed": True, "reason": f"{file} is {file_size} MBs, which exceeds allowed 50 MBs"}

        try:
            with open(file) as f:
                json_file = json.load(f)
        except ValueError as e:
            info = f'Skipped {file}. Unable to load as json. {str(e)}'
            logging.info(info)
            return {"file": file, "skipped": True, 'reason': info}  # Not a json

        resource_type = json_file.get('resourceType')
        if not resource_type:
            info = f'Failed {file}. No resourceType defined.'
            logging.info(info)
            return {"file": file, "failed": True, 'reason': info}

        if resources and resource_type not in resources:
            info = f'Skipped {file}. Type {resource_type} is not in {resources}.'
            logging.debug(info)
            return {'file': file, 'skipped': True, 'reason': info}

        logging.info(f'Importing {file}')
        response = post(f"{target}/{resource_type}/", secret, json_file)

        if response.status_code != 201:
            summary = response_summary(response)
            error = f'Failed to create {file}. {summary} {response_summary(response)}'
            logging.error(error)
            return {'file': file, 'failed': True, 'reason': error}
    except Exception as e:
        error = f'Failed to create {file}. {str(e)} {response_summary(response)}'
        logging.exception(error)
        return {'file': file, 'failed': True, 'reason': error}

    logging.info(f'Imported {file}')
    json_response = response.json()
    if validate:
        diff = validate_jsons(json_file, json_response)
        if diff:
            logging.error(f'Failed validation for {file} at {json_response.get("url", None)}: {diff}')
            invalid = {'file': file, 'url': json_response.get('url', None), 'diff': diff}
        else:
            invalid = None
    else:
        invalid = None
    return {'file': file, 'imported': file, 'url': json_response.get('url', None), 'invalid': invalid}


def import_resources(source, target, secret, resources=['CodeSystem', 'ValueSet', 'ConceptMap'], clear=None,
                     validate=False):
    if clear:
        clear = clear.rstrip('/')

        result = get(clear, secret)
        if result.status_code != 404:
            result = delete(clear, secret)
            if result.get('failed'):
                return {'imported': [], 'skipped': 0, 'total': 0, 'errors': [result.get('reason')]}

            wait_time = 0.1
            max_wait_time = 120
            response = get(clear, secret)
            while response.status_code != 404:
                logging.info(f'Waiting {wait_time}s for {clear} to be deleted.')
                time.sleep(wait_time)
                if wait_time >= max_wait_time:
                    return {'imported': [], 'skipped': 0, 'total': 0, 'errors': [f"Unable to clear {clear} due to "
                                                                                 f"timeout on waiting for deletion."]}
                wait_time *= 2
                if wait_time > max_wait_time:
                    wait_time = max_wait_time

                response = get(clear, secret)

        uri = clear.rsplit('/', 1)
        logging.info(f'Creating {clear}')
        result = post(uri[0] + '/', secret, {'id': uri[1], 'name': uri[1]})
        if result.status_code != 201:
            error = f"Unable to create {clear} [{result.status_code}] {result.content}."
            logging.error(error)
            return {'imported': [], 'skipped': 0, 'total': 0, 'errors': [error]}

    if not source:
        return

    if not resources:
        resources = ['CodeSystem', 'ValueSet', 'ConceptMap']
        
    if os.path.isdir(source):
        directory = pathlib.Path(source)
        pattern = "**/*"
        files = filter(lambda item: item.is_file(), directory.glob(pattern))
    elif os.path.isfile(source):
        files = [source]
    else:
        raise FileNotFoundError("Source must be a file or directory")

    resources_count = 0
    more = ''
    for _ in files:
        if resources_count > 10000:
            more = '+'
            break  # Stop counting to save time
        else:
            resources_count += 1
    logging.info(f'Discovered {resources_count}{more} files in {source}.')

    imported = {}
    skipped = {}
    failed = {}
    invalid = {}

    queue_limit = 50
    futures = set()

    resource_types = list()
    if 'CodeSystem' in resources:
        resource_types.append('CodeSystem')
    if 'ValueSet' in resources:
        resource_types.append('ValueSet')
    if 'ConceptMap' in resources:
        resource_types.append('ConceptMap')

    for resource_type in resources:
        logging.info(f'Importing resources of {resource_type} type...')
        with ThreadPoolExecutor(max_workers=5) as executor:
            total_count = 0
            imported_type_count = 0
            invalid_type_count = 0
            failed_type_count = 0
            skipped_type_count = 0
            files = filter(lambda item: item.is_file(), directory.glob(pattern))
            completed = set()
            file = next(files, None)
            while file is not None:
                if len(futures) >= queue_limit:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                if completed is None:
                    completed = set()

                futures.add(executor.submit(create, file, target, secret, [resource_type], validate))
                total_count += 1
                if total_count > 10000:
                    resources_count = total_count  # Adjust resources count
                logging.info(f'Processing {total_count}/{resources_count}. Imported {imported_type_count} '
                             f'({invalid_type_count} invalid), skipped {skipped_type_count}, '
                             f'failed {failed_type_count} resources of type {resource_type}.')

                file = next(files, None)
                if file is None:
                    # No more files, so wait for all tasks to finish
                    all_completed, futures = wait(futures, return_when=ALL_COMPLETED)
                    completed = completed.union(all_completed)

                for future in completed:
                    result = future.result()
                    result_file = str(result.get('file'))

                    if result.get('imported') is not None:
                        imported_type_count += 1
                        if result_file not in imported:
                            imported[result_file] = []
                        imported[result_file].append(result.get('imported'))
                        if result.get('invalid', None):
                            invalid_type_count += 1
                            if result_file not in invalid:
                                invalid[result_file] = []
                            invalid[result_file].append(result.get('invalid'))
                    elif result.get('skipped', False):
                        if result_file not in skipped:
                            skipped[result_file] = []
                        skipped[result_file].append(result.get('reason'))
                        skipped_type_count += 1
                    elif result.get('failed', False):
                        failed_type_count += 1
                        if result_file not in failed:
                            failed[result_file] = []
                        failed[result_file].append(result.get('reason'))

                completed = set()

        logging.info(f'Done importing resources of {resource_type} type. Imported {imported_type_count} '
                     f'({invalid_type_count} invalid), skipped {skipped_type_count}, failed {failed_type_count}\n.')

    invalid_message = ''
    if failed:
        failures_msg = []
        for failures in failed.values():
            failures_msg.append("\n\n".join(failures))
        error_message = f'There were {len(failed)} errors:\n' + "".join(failures_msg)
        logging.error(error_message)
    if invalid:
        validation_message = f'There were {len(invalid)} invalid:\n'
        for item in invalid.values():
            for invalid_item in item:
                validation_message += f'Failed validation for {invalid_item.get("file")} at ' \
                                  f'{invalid_item.get("url", None)}: {invalid_item.get("diff", None)}\n\n'
        logging.error(validation_message)
        invalid_message = f' ({len(invalid)} invalid)'

    for file in imported.keys():
        skipped.pop(file, None)
        failed.pop(file, None)


    logging.info(f'Processed {resources_count} resources. '
                 f'Imported {len(imported)}{invalid_message}, skipped {len(skipped)}, '
                 f'failed {len(failed)}.')

    return {
        "imported": imported,
        "invalid": invalid,
        "skipped": skipped,
        "total": resources_count,
        "failed": failed
    }


def main():
    logging.getLogger().setLevel(logging.INFO)
    arg_parser = argparse.ArgumentParser(description="Tool for importing resources.")
    arg_parser.add_argument("-f", "--from", dest="from_", help="File, directory or URL to import", required=False,
                            type=str)
    arg_parser.add_argument("-t", "--to", help="API path pointing to a container for imported resources, e.g. "
                            "https://fhir.openconceptlab.org/orgs/test_org", required=True, type=str)
    arg_parser.add_argument("-c", "--clear", help="Drop and recreate the given org for imported resources",
                            default=None, type=str)
    arg_parser.add_argument("-s", "--secret", help="API token", required=False, type=str)
    arg_parser.add_argument("-r", "--resources", help="Types of resources to import", required=False, default=[],
                            choices=['CodeSystem', 'ValueSet', 'ConceptMap'], nargs="*")
    arg_parser.add_argument("-v", "--validate", help="Validate imported resources", required=False, action='store_true')

    args = arg_parser.parse_args()

    import_resources(args.from_, args.to, args.secret, args.resources, args.clear, args.validate)

    return 0


if __name__ == "__main__":
    sys.exit(main())

