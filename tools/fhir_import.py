import argparse
import json
import os
import pathlib
import time

import requests
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def delete(target, secret):
    print(f'Deleting: {target}')

    try:
        response = None

        response = requests.delete(
            target,
            headers={'Authorization': f'Token {secret}'})

        if response.status_code != 200:
            error = f'{target} failed to delete due to: [{response.status_code}]'
            if response.content:
                error += f' {response.content}'
            print(error)
            return {'failed': True, 'reason': error}
    except Exception as e:
        error = f'{target} failed to delete due to: {str(e)}.'
        if response:
            error = error + f' [{response.status_code}] {response.content}'
        print(error)
        return {'failed': True, 'reason': error}

    print(f'Deleted: {target}')
    return None


def create(file, target, secret, resources=[]):
    print(f'Importing file: {file}')

    try:
        file_stats = os.stat(file)
        file_size = file_stats / (1024 * 1024)
        if file_size > 50:
            return {"skipped": True, "reason": f"{file} is {file_size} MBs, which exceeds allowed 50 MBs"}

        try:
            with open(file) as f:
                json_file = json.load(f)
        except ValueError as e:
            return {"skipped": True, 'reason': f'Unable to load as json: {str(e)}'}  # Not a json

        resource_type = json_file.get('resourceType')
        if not resource_type:
            return {"skipped": True, 'reason': f'No resourceType defined in file {file}'}

        if resources and resource_type not in resources:
            return {'skipped': True, 'reason': f"{file} is {resource_type}, which is not in {resources}"}

        response = requests.post(
            f"{target}/{resource_type}/",
            json=json_file,
            headers={'Authorization': f'Token {secret}'})

        if response.status_code != 201:
            error = f'{file} failed to import due to: [{response.status_code}]'
            if response.content:
                error += f' {response.content}'
            print(error)
            return {'failed': True, 'reason': error}
    except Exception as e:
        error = f'{file} failed to import due to: {str(e)}.'
        if response:
            error = error + f' [{response.status_code}] {response.content}'
        print(error)
        return {'failed': True, 'reason': error}

    print(f'Imported file: {file}')
    return {'imported': True}


def import_resources(source, target, secret, resources=['CodeSystem', 'ValueSet', 'ConceptMap'], clear=None, depth=0):
    if not resources:
        resources = ['CodeSystem', 'ValueSet', 'ConceptMap']
        
    if os.path.isdir(source):
        directory = pathlib.Path(source)
        pattern = "*" + "/*" * depth
        files = filter(lambda item: item.is_file(), directory.iglob(pattern, recursive=True))
    elif os.path.isfile(source):
        files = [source]
    else:
        raise FileNotFoundError("Source must be a file or directory")

    if clear:
        auth = {'Authorization': f'Token {secret}'}

        clear = clear.rstrip('/')

        result = requests.get(clear, headers=auth, timeout=10)
        if result.status_code != 404:
            result = delete(clear, headers=auth)
            if result.get('failed'):
                return {'imported': 0, 'skipped': 0, 'total': 0, 'errors': [result.get('reason')]}

            wait_time = 0.1
            max_wait_time = 120
            while requests.get(clear, headers=auth, timeout=10) != 404:
                time.sleep(wait_time)
                wait_time *= 2
                if wait_time > max_wait_time:
                    return {'imported': 0, 'skipped': 0, 'total': 0, 'errors': [f"Unable to clear {clear} due to "
                                                                                f"timeout on waiting for deletion."]}

        uri = clear.rsplit('/', 1)
        result = requests.post(
                uri[0],
                json={'id': uri[1], 'name': uri[1]},
                headers=auth)
        if result.status_code != 202:
            return {'imported': 0, 'skipped': 0, 'total': 0, 'errors': [f"Unable to create {clear} due to "
                                                                        f"{result.status_code}: {result.content}"]}

    resources_count = 0
    for _ in files:
        if resources_count > 1000:
            break  # Stop counting to save time
        else:
            resources_count += 1
    print(f'Discovered {resources_count} files.')

    total_count = 0
    imported_count = 0
    skipped_count = 0
    errors_count = 0
    errors = []

    queue_limit = 50
    futures = set()

    resource_types = list()
    if 'CodeSystem' in resources:
        resource_types.append('CodeSystem')
    if 'ValueSet' in resources:
        resource_types.append('ValueSet')
    if 'ConceptMap' in resources:
        resource_types.append('ConceptMap')

    for resourceType in resources:
        with ThreadPoolExecutor(max_workers=5) as executor:
            for file in files:
                if len(futures) >= queue_limit:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                futures.add(executor.submit(create, file, target, secret, [resourceType]))

                for future in completed:
                    result = future.result()
                    total_count += 1
                    if total_count > 1000:
                        resources_count += 1  # Adjust estimated total count

                    if result.get('imported'):
                        imported_count += 1
                    elif result.get('skipped'):
                        skipped_count += 1
                    elif result.get('error'):
                        errors_count += 1
                        errors.append(result.get('error'))
                    print(f'Processed {skipped_count + imported_count} files out of {resources_count}. '
                          f'Imported {imported_count} resources and failed on {errors_count}.')

    print(f'Processed {skipped_count + imported_count} files out of {resources_count}. '
          f'Imported {imported_count} resources and failed on {errors_count}.')
    if errors:
        print(f'There were {errors_count} failures due to:')
        for error in errors:
            print(error)

    return {
        "imported": total_count,
        "skipped": skipped_count,
        "total": resources_count,
        "errors": errors
    }


def main():
    arg_parser = argparse.ArgumentParser(description="Tool for importing FHIR resources.")
    arg_parser.add_argument("-f", "--from", help="File, directory or URL to import", required=True, type=str)
    arg_parser.add_argument("-d", "--depth", help="Read files from subdirectories recursively up to specified depth",
                            required=False, default=0, type=int)
    arg_parser.add_argument("-t", "--to", help="API path pointing to a container for imported resources, e.g. "
                            "https://fhir.openconceptlab.org/orgs/test_org", required=True, type=str)
    arg_parser.add_argument("-c", "--clear", help="Drop and recreate the given org for imported resources",
                            default=None, type=str)
    arg_parser.add_argument("-s", "--secret", help="API token", required=False, type=str)
    arg_parser.add_argument("-r", "--resources", help="Types of resources to import", required=False, default=[],
                            choices=['CodeSystem', 'ValueSet', 'ConceptMap'], nargs="*")

    args = arg_parser.parse_args()

    import_resources(args.from_, args.to, args.secret, args.resources, args.clear, args.depth)

    return 0


if __name__ == "__main__":
    sys.exit(main())

