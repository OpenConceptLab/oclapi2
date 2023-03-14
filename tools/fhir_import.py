import argparse
import json
import os
import requests
import sys
from concurrent.futures import ThreadPoolExecutor

args = None


def create(test_file):
    print(f'Importing file: {test_file}')

    try:
        response = None
        with open(test_file) as f:
            json_file = json.load(f)

        response = requests.post(
            f"{args.path}/{json_file['resourceType']}/",
            json=json_file,
            headers={'Authorization': f'Token {args.token}'})

        if response.status_code != 201:
            error = f'{test_file} failed to import due to: [{response.status_code}]'
            if response.content:
                error += f' {response.content}'
            print(error)
            return error
    except Exception as e:
        error = f'{test_file} failed to import due to: {str(e)}.'
        if response:
            error = error + f' [{response.status_code}] {response.content}'
        print(error)
        return error

    print(f'Imported file: {test_file}')
    return None


def import_resource(resource_type, resources):
    total_count = 0
    errors_count = 0
    errors = []

    if args.dir:
        resources = filter(lambda file: file.startswith(resource_type), resources)
        resources = [os.path.join(args.dir, resource) for resource in resources]
    resources_count = len(resources)

    print(f'Importing {resources_count} {resource_type}s...')
    with ThreadPoolExecutor(max_workers=5) as executor:
        for result in executor.map(create, resources):
            total_count = total_count + 1
            if result:
                errors_count = errors_count + 1
                errors.append(result)
            print(f'Processed {total_count} out of {resources_count} {resource_type}s ({errors_count} errors).')

    print(f'Imported {total_count-errors_count} out of {resources_count} {resource_type}s.')
    if errors:
        print(f'There were {errors_count} failures due to:')
        for error in errors:
            print(error)


def main():
    arg_parser = argparse.ArgumentParser(description="Tool for importing FHIR resources.")
    arg_parser.add_argument("-f", "--file", help="File with a single resource to import",
                            required=False)
    arg_parser.add_argument("-d", "--dir", help="Directory containing files with individual resources to import",
                            required=False)
    arg_parser.add_argument("-p", "--path", help="API path pointing to a container for imported resources"
                                                 ", e.g. https://fhir.openconceptlab.org/orgs/test_org", required=True)
    arg_parser.add_argument("-t", "--token", help="API token", required=True)
    arg_parser.add_argument("-r", "--resource", help="Type of resources to import", required=False,
                            choices=['CodeSystem', 'ValueSet', 'ConceptMap'])

    global args
    args = arg_parser.parse_args()

    files = None
    if args.dir:
        files = os.listdir(args.dir)
    if args.file:
        files = [args.file]
    if not files:
        arg_parser.print_help()
        print('You must specify --dir or --file to import.')

    if not args.resource or args.resource == 'CodeSystem':
        import_resource('CodeSystem', files)
    if not args.resource or args.resource == 'ValueSet':
        import_resource('ValueSet', files)
    if not args.resource or args.resource == 'ConceptMap':
        import_resource('ConceptMap', files)

    return 0


if __name__ == "__main__":
    sys.exit(main())

