import json
import os
import random
import tempfile
import uuid
import zipfile
from urllib import parse

import requests
from dateutil import parser
from django.conf import settings
from django.urls import NoReverseMatch, reverse, get_resolver, resolve, Resolver404
from djqscsv import csv_file_for
from pydash import flatten
from requests.auth import HTTPBasicAuth
from rest_framework.utils import encoders

from core.common.constants import UPDATED_SINCE_PARAM, BULK_IMPORT_QUEUES_COUNT
from core.common.services import S3


def get_latest_dir_in_path(path):  # pragma: no cover
    all_sub_dirs = [path + d for d in os.listdir(path) if os.path.isdir(path + d)]
    return max(all_sub_dirs, key=os.path.getmtime)


def cd_temp():  # pragma: no cover
    cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    return cwd


def write_csv_to_s3(data, is_owner, **kwargs):  # pragma: no cover
    cwd = cd_temp()
    csv_file = csv_file_for(data, **kwargs)
    csv_file.close()
    zip_file_name = csv_file.name + '.zip'
    with zipfile.ZipFile(zip_file_name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.write(csv_file.name)

    file_path = get_downloads_path(is_owner) + zip_file_name
    S3.upload_file(file_path)
    os.chdir(cwd)
    return S3.url_for(file_path)


def compact_dict_by_values(_dict):
    copied_dict = _dict.copy()
    for key, value in copied_dict.copy().items():
        if not value:
            copied_dict.pop(key)

    return copied_dict


def get_downloads_path(is_owner):  # pragma: no cover
    return 'downloads/creator/' if is_owner else 'downloads/reader/'


def get_csv_from_s3(filename, is_owner):  # pragma: no cover
    filename = get_downloads_path(is_owner) + filename + '.csv.zip'
    return S3.url_for(filename)


def reverse_resource(resource, viewname, args=None, kwargs=None, **extra):
    """
    Generate the URL for the view specified as viewname of the object specified as resource.
    """
    kwargs = kwargs or {}
    parent = resource
    while parent is not None:
        if not hasattr(parent, 'get_url_kwarg'):
            return NoReverseMatch('Cannot get URL kwarg for %s' % resource)  # pragma: no cover

        if parent.is_versioned and not parent.is_head:
            from core.collections.models import Collection
            from core.sources.models import Source
            if isinstance(parent, (Source, Collection)):
                head = parent.get_latest_version()
            else:
                head = parent.head
            kwargs.update({head.get_url_kwarg(): head.mnemonic, parent.get_url_kwarg(): parent.version})
            if parent.get_resource_url_kwarg() not in kwargs:
                kwargs.update({parent.get_resource_url_kwarg(): parent.mnemonic})
        else:
            kwargs.update({parent.get_url_kwarg(): parent.mnemonic})
        parent = parent.parent if hasattr(parent, 'parent') else None
        allowed_kwargs = get_kwargs_for_view(viewname)
        for key in kwargs.copy():
            if key not in allowed_kwargs:
                kwargs.pop(key)

    return reverse(viewname=viewname, args=args, kwargs=kwargs, **extra)


def reverse_resource_version(resource, viewname, args=None, kwargs=None, **extra):
    """
    Generate the URL for the view specified as viewname of the object that is
    versioned by the object specified as resource.
    Assumes that resource extends ResourceVersionMixin, and therefore has a versioned_object attribute.
    """
    from core.collections.models import Collection
    from core.sources.models import Source
    if isinstance(resource, (Source, Collection)):
        head = resource.get_latest_version()
    else:
        head = resource.head

    kwargs = kwargs or {}
    kwargs.update({
        resource.get_url_kwarg(): resource.version,
        head.get_url_kwarg(): head.mnemonic,
    })
    resource_url_kwarg = resource.get_resource_url_kwarg()

    if resource_url_kwarg not in kwargs:
        kwargs[resource_url_kwarg] = resource.mnemonic

    return reverse_resource(resource, viewname, args, kwargs, **extra)


def get_kwargs_for_view(view_name):
    resolver = get_resolver()
    patterns = resolver.reverse_dict.getlist(view_name)
    return list(set(flatten([p[0][0][1] for p in patterns])))


def parse_updated_since_param(params):
    return parse_updated_since(params.get(UPDATED_SINCE_PARAM))


def parse_updated_since(updated_since):  # pragma: no cover
    if updated_since:
        try:
            return parser.parse(updated_since)
        except ValueError:
            pass
    return None


def parse_boolean_query_param(request, param, default=None):
    val = request.query_params.get(param, default)
    if val is None:
        return None
    for boolean in [True, False]:
        if str(boolean).lower() == val.lower():
            return boolean
    return None  # pragma: no cover


def get_query_params_from_url_string(url):
    try:
        return dict(parse.parse_qsl(parse.urlsplit(url).query))
    except:  # pylint: disable=bare-except  # pragma: no cover
        return dict()


def is_valid_uri(uri):
    try:
        resolve(uri)
        return True
    except Resolver404:
        pass

    return False


def get_class(kls):
    parts = kls.split('.')
    module = ".".join(parts[:-1])
    _module = __import__(module)
    for comp in parts[1:]:
        _module = getattr(_module, comp)
    return _module


def write_export_file(
        version, resource_type, resource_serializer_type, logger
):  # pylint: disable=too-many-statements,too-many-locals
    cwd = cd_temp()
    logger.info('Writing export file to tmp directory: %s' % cwd)

    logger.info('Found %s version %s.  Looking up resource...' % (resource_type, version.version))
    resource = version.head
    logger.info('Found %s %s.  Serializing attributes...' % (resource_type, resource.mnemonic))

    resource_serializer = get_class(resource_serializer_type)(version)
    data = resource_serializer.data
    resource_string = json.dumps(data, cls=encoders.JSONEncoder)
    logger.info('Done serializing attributes.')

    batch_size = 1000
    concepts_qs = version.concepts
    mappings_qs = version.mappings
    if resource_type != 'collection':
        concepts_qs = concepts_qs.filter(is_active=True)
        mappings_qs = mappings_qs.filter(is_active=True)

    total_concepts = concepts_qs.count()
    total_mappings = mappings_qs.count()

    with open('export.json', 'w') as out:
        out.write('%s, "concepts": [' % resource_string[:-1])

    if total_concepts:
        logger.info(
            '%s has %d concepts. Getting them in batches of %d...' % (resource_type.title(), total_concepts, batch_size)
        )
        concept_serializer_class = get_class('core.concepts.serializers.ConceptVersionDetailSerializer')
        for start in range(0, total_concepts, batch_size):
            end = min(start + batch_size, total_concepts)
            logger.info('Serializing concepts %d - %d...' % (start+1, end))
            concept_versions = concepts_qs.all()[start:end]
            concept_serializer = concept_serializer_class(concept_versions, many=True)
            concept_data = concept_serializer.data
            concept_string = json.dumps(concept_data, cls=encoders.JSONEncoder)
            concept_string = concept_string[1:-1]
            with open('export.json', 'a') as out:
                out.write(concept_string)
                if end != total_concepts:
                    out.write(', ')
        logger.info('Done serializing concepts.')
    else:
        logger.info('%s has no concepts to serialize.' % (resource_type.title()))

    with open('export.json', 'a') as out:
        out.write('], "mappings": [')

    if total_mappings:
        logger.info(
            '%s has %d mappings. Getting them in batches of %d...' % (resource_type.title(), total_mappings, batch_size)
        )
        mapping_serializer_class = get_class('core.mappings.serializers.MappingDetailSerializer')
        for start in range(0, total_mappings, batch_size):
            end = min(start + batch_size, total_mappings)
            logger.info('Serializing mappings %d - %d...' % (start+1, end))
            mappings = mappings_qs.all()[start:end]
            mapping_serializer = mapping_serializer_class(mappings, many=True)
            mapping_data = mapping_serializer.data
            mapping_string = json.dumps(mapping_data, cls=encoders.JSONEncoder)
            mapping_string = mapping_string[1:-1]
            with open('export.json', 'a') as out:
                out.write(mapping_string)
                if end != total_mappings:
                    out.write(', ')
        logger.info('Done serializing mappings.')
    else:
        logger.info('%s has no mappings to serialize.' % (resource_type.title()))

    with open('export.json', 'a') as out:
        out.write(']}')

    with zipfile.ZipFile('export.zip', 'w', zipfile.ZIP_DEFLATED) as _zip:
        _zip.write('export.json')

    file_path = os.path.abspath('export.zip')
    logger.info(file_path)
    logger.info('Done compressing.  Uploading...')

    s3_key = version.export_path
    S3.upload_file(key=s3_key, file_path=file_path, binary=True)
    uploaded_path = S3.url_for(s3_key)
    logger.info('Uploaded to %s.' % uploaded_path)
    os.chdir(cwd)


def get_base_url():
    if settings.ENV == 'development':
        return "http://localhost:8000"

    return "https://api.{}2.openconceptlab.org".format(settings.ENV.lower())


def to_snake_case(string):
    # from https://www.geeksforgeeks.org/python-program-to-convert-camel-case-string-to-snake-case/
    return ''.join(['_' + i.lower() if i.isupper() else i for i in string]).lstrip('_')


def to_camel_case(string):
    # from https://www.geeksforgeeks.org/python-convert-snake-case-string-to-camel-case/?ref=rp
    temp = string.split('_')
    return str(temp[0] + ''.join(ele.title() for ele in temp[1:]))


def parse_bulk_import_task_id(task_id):
    """
    Used to parse bulk import task id, which is in format '{uuid}-{username}~{queue}'.
    :param task_id:
    :return: dictionary with uuid, username, queue
    """
    task = dict(uuid=task_id[:37])
    username = task_id[37:]
    queue_index = username.find('~')
    if queue_index != -1:
        queue = username[queue_index + 1:]
        username = username[:queue_index]
    else:
        queue = 'default'

    task['username'] = username
    task['queue'] = queue
    return task


def flower_get(url):
    """
    Returns a flower response from the given endpoint url.
    :param url:
    :return:
    """
    return requests.get('http://flower:5555/' + url, auth=HTTPBasicAuth(settings.FLOWER_USER, settings.FLOWER_PWD))


def task_exists(task_id):
    """
    This method is used to check Celery Task validity when state is PENDING. If task exists in
    Flower then it's considered as Valid task otherwise invalid task.
    """
    flower_response = flower_get('api/task/info/' + task_id)
    return bool(flower_response and flower_response.status_code == 200 and flower_response.text)


def queue_bulk_import(to_import, import_queue, username, update_if_exists):
    """
    Used to queue bulk imports. It assigns a bulk import task to a specified import queue or a random one.
    If requested by the root user, the bulk import goes to the priority queue.

    :param to_import:
    :param import_queue:
    :param username:
    :param update_if_exists:
    :return: task
    """
    task_id = str(uuid.uuid4()) + '-' + username

    if username in ['root', 'ocladmin']:
        queue_id = 'bulk_import_root'
        task_id += '~priority'
    elif import_queue:
        # assigning to one of 5 queues processed in order
        queue_id = 'bulk_import_' + str(hash(username + import_queue) % BULK_IMPORT_QUEUES_COUNT)
        task_id += '~' + import_queue
    else:
        # assigning randomly to one of 5 queues processed in order
        queue_id = 'bulk_import_' + str(random.randrange(0, BULK_IMPORT_QUEUES_COUNT))
        task_id += '~default'

    from core.common.tasks import bulk_import

    return bulk_import.apply_async((to_import, username, update_if_exists), task_id=task_id, queue=queue_id)


def drop_version(expression):
    return '/'.join(expression.split('/')[0:7]) + '/'