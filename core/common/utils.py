import json
import os
import random
import tempfile
import uuid
import zipfile
from collections import MutableMapping, OrderedDict  # pylint: disable=no-name-in-module
from urllib import parse

import requests
from celery_once.helpers import queue_once_key
from dateutil import parser
from django.conf import settings
from django.urls import NoReverseMatch, reverse, get_resolver, resolve, Resolver404
from djqscsv import csv_file_for
from pydash import flatten
from requests.auth import HTTPBasicAuth
from rest_framework.utils import encoders

from core.common.constants import UPDATED_SINCE_PARAM, BULK_IMPORT_QUEUES_COUNT, TEMP
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

    key = get_downloads_path(is_owner) + zip_file.filename
    S3.upload_file(key=key, file_path=os.path.abspath(zip_file.filename), binary=True)
    os.chdir(cwd)
    return S3.url_for(key)


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

    if S3.exists(filename):
        return S3.url_for(filename)

    return None


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

            if head:
                kwargs.update({head.get_url_kwarg(): head.mnemonic})

            kwargs.update({parent.get_url_kwarg(): parent.version})
            parent_resource_url_kwarg = parent.get_resource_url_kwarg()
            if parent_resource_url_kwarg not in kwargs:
                kwargs.update({parent_resource_url_kwarg: parent.mnemonic})
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
    if head:
        kwargs.update({head.get_url_kwarg(): head.mnemonic})

    kwargs.update({resource.get_url_kwarg(): resource.version})
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
):  # pylint: disable=too-many-statements,too-many-locals,too-many-branches
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
    is_collection = resource_type == 'collection'
    if not is_collection:
        concepts_qs = concepts_qs.filter(is_active=True)
        mappings_qs = mappings_qs.filter(is_active=True)

    total_concepts = concepts_qs.count()
    total_mappings = mappings_qs.count()

    with open('export.json', 'w') as out:
        out.write('%s, "concepts": [' % resource_string[:-1])

    resource_name = resource_type.title()

    if total_concepts:
        logger.info(
            '%s has %d concepts. Getting them in batches of %d...' % (resource_name, total_concepts, batch_size)
        )
        concept_serializer_class = get_class('core.concepts.serializers.ConceptVersionDetailSerializer')
        for start in range(0, total_concepts, batch_size):
            end = min(start + batch_size, total_concepts)
            logger.info('Serializing concepts %d - %d...' % (start+1, end))
            concept_versions = concepts_qs.order_by('-id').prefetch_related(
                'names', 'descriptions').select_related('parent__organization', 'parent__user')[start:end]
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
        logger.info('%s has no concepts to serialize.' % resource_name)

    if is_collection:
        references_qs = version.references
        total_references = references_qs.count()

        with open('export.json', 'a') as out:
            out.write('], "references": [')
        if total_references:
            logger.info(
                '%s has %d references. Getting them in batches of %d...' % (
                    resource_name, total_references, batch_size)
            )
            reference_serializer_class = get_class('core.collections.serializers.CollectionReferenceSerializer')
            for start in range(0, total_references, batch_size):
                end = min(start + batch_size, total_references)
                logger.info('Serializing references %d - %d...' % (start + 1, end))
                references = references_qs.order_by('-id').filter()[start:end]
                reference_serializer = reference_serializer_class(references, many=True)
                reference_string = json.dumps(reference_serializer.data, cls=encoders.JSONEncoder)
                reference_string = reference_string[1:-1]
                with open('export.json', 'a') as out:
                    out.write(reference_string)
                    if end != total_references:
                        out.write(', ')
            logger.info('Done serializing references.')
        else:
            logger.info('%s has no references to serialize.' % resource_name)

    with open('export.json', 'a') as out:
        out.write('], "mappings": [')

    if total_mappings:
        logger.info(
            '%s has %d mappings. Getting them in batches of %d...' % (resource_name, total_mappings, batch_size)
        )
        mapping_serializer_class = get_class('core.mappings.serializers.MappingDetailSerializer')
        for start in range(0, total_mappings, batch_size):
            end = min(start + batch_size, total_mappings)
            logger.info('Serializing mappings %d - %d...' % (start+1, end))
            mappings = mappings_qs.order_by('-id').select_related(
                'parent__organization', 'parent__user', 'from_concept', 'to_concept',
                'from_source__organization', 'from_source__user',
                'to_source__organization', 'to_source__user',
            )[start:end]
            reference_serializer = mapping_serializer_class(mappings, many=True)
            reference_data = reference_serializer.data
            reference_string = json.dumps(reference_data, cls=encoders.JSONEncoder)
            reference_string = reference_string[1:-1]
            with open('export.json', 'a') as out:
                out.write(reference_string)
                if end != total_mappings:
                    out.write(', ')
        logger.info('Done serializing mappings.')
    else:
        logger.info('%s has no mappings to serialize.' % (resource_name))

    with open('export.json', 'a') as out:
        out.write(']}')

    with zipfile.ZipFile('export.zip', 'w', zipfile.ZIP_DEFLATED) as _zip:
        _zip.write('export.json')

    file_path = os.path.abspath('export.zip')
    logger.info(file_path)
    logger.info('Done compressing.  Uploading...')

    s3_key = version.export_path
    S3.upload_file(
        key=s3_key, file_path=file_path, binary=True, metadata=dict(ContentType='application/zip'),
        headers={'content-type': 'application/zip'}
    )
    uploaded_path = S3.url_for(s3_key)
    logger.info('Uploaded to %s.' % uploaded_path)
    os.chdir(cwd)


def get_api_base_url():
    return settings.API_BASE_URL


def get_api_internal_base_url():
    return settings.API_INTERNAL_BASE_URL


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


def flower_get(url, **kwargs):
    """
    Returns a flower response from the given endpoint url.
    :param url:
    :return:
    """
    return requests.get(
        'http://%s:%s/%s' % (settings.FLOWER_HOST, settings.FLOWER_PORT, url),
        auth=HTTPBasicAuth(settings.FLOWER_USER, settings.FLOWER_PASSWORD),
        **kwargs
    )


def es_get(url, **kwargs):
    """
    Returns a flower response from the given endpoint url.
    :param url:
    :return:
    """
    return requests.get(
        'http://%s:%s/%s' % (settings.ES_HOST, settings.ES_PORT, url),
        **kwargs
    )


def task_exists(task_id):
    """
    This method is used to check Celery Task validity when state is PENDING. If task exists in
    Flower then it's considered as Valid task otherwise invalid task.
    """
    flower_response = flower_get('api/task/info/' + task_id)
    return bool(flower_response and flower_response.status_code == 200 and flower_response.text)


def queue_bulk_import(  # pylint: disable=too-many-arguments
        to_import, import_queue, username, update_if_exists, threads=None, inline=False, sub_task=False
):
    """
    Used to queue bulk imports. It assigns a bulk import task to a specified import queue or a random one.
    If requested by the root user, the bulk import goes to the priority queue.

    :param to_import:
    :param import_queue:
    :param username:
    :param update_if_exists:
    :param threads:
    :param inline:
    :param sub_task:
    :return: task
    """
    task_id = str(uuid.uuid4()) + '-' + username

    if username in ['root', 'ocladmin'] and import_queue != 'concurrent':
        queue_id = 'bulk_import_root'
        task_id += '~priority'
    elif import_queue == 'concurrent':
        queue_id = import_queue
        task_id += '~' + import_queue
    elif import_queue:
        # assigning to one of 5 queues processed in order
        queue_id = 'bulk_import_' + str(hash(username + import_queue) % BULK_IMPORT_QUEUES_COUNT)
        task_id += '~' + import_queue
    else:
        # assigning randomly to one of 5 queues processed in order
        queue_id = 'bulk_import_' + str(random.randrange(0, BULK_IMPORT_QUEUES_COUNT))
        task_id += '~default'

    if inline:
        if sub_task:
            from core.common.tasks import bulk_import_parts_inline
            return bulk_import_parts_inline.apply_async(
                (to_import, username, update_if_exists), task_id=task_id, queue=queue_id
            )

        if threads:
            from core.common.tasks import bulk_import_parallel_inline
            return bulk_import_parallel_inline.apply_async(
                (to_import, username, update_if_exists, threads), task_id=task_id, queue=queue_id
            )
        from core.common.tasks import bulk_import_inline
        return bulk_import_inline.apply_async(
            (to_import, username, update_if_exists), task_id=task_id, queue=queue_id
        )

    from core.common.tasks import bulk_import
    return bulk_import.apply_async((to_import, username, update_if_exists), task_id=task_id, queue=queue_id)


def drop_version(expression):
    if not expression:
        return expression

    parts = expression.split('/')

    if len(parts) <= 4:
        return expression

    resource = parts[-4]
    name = parts[-3]
    version = parts[-2]
    if resource in ['concepts', 'mappings', 'sources', 'collections'] and name and version:
        return '/'.join(parts[:-2]) + '/'

    return expression


def is_versioned_uri(expression):
    return expression != drop_version(expression)


def to_parent_uri(expression):
    splitter = None
    if '/concepts/' in expression:
        splitter = '/concepts/'
    elif '/mappings/' in expression:
        splitter = '/mappings/'

    if splitter:
        return expression.split(splitter)[0] + '/'

    return expression


def separate_version(expression):
    versionless_expression = drop_version(expression)
    if expression != versionless_expression:
        return expression.replace(versionless_expression, '').replace('/', ''), versionless_expression

    return None, expression


def generate_temp_version():
    return "{}-{}".format(TEMP, str(uuid.uuid4())[:8])


def jsonify_safe(value):
    if isinstance(value, dict):
        return value

    try:
        return json.loads(value)
    except:  # pylint: disable=bare-except
        return value


def web_url():
    env = settings.ENV
    if not env or env in ['development', 'ci']:
        return 'http://localhost:4000'

    if env == 'production':
        return "https://app.openconceptlab.org"

    return "https://app.{}.openconceptlab.org".format(env)


def get_resource_class_from_resource_name(resource):  # pylint: disable=too-many-return-statements
    if not resource:
        return resource

    name = resource.lower()
    if name in ['concepts', 'concept']:
        from core.concepts.models import Concept
        return Concept
    if name in ['mappings', 'mapping']:
        from core.mappings.models import Mapping
        return Mapping
    if name in ['users', 'user', 'user_profiles', 'user_profile', 'userprofiles', 'userprofile']:
        from core.users.models import UserProfile
        return UserProfile
    if name in ['orgs', 'org', 'organizations', 'organization']:
        from core.orgs.models import Organization
        return Organization
    if name in ['sources', 'source']:
        from core.sources.models import Source
        return Source
    if name in ['collections', 'collection']:
        from core.collections.models import Collection
        return Collection

    return None


def get_content_type_from_resource_name(resource):
    model = get_resource_class_from_resource_name(resource)
    if model:
        from django.contrib.contenttypes.models import ContentType
        return ContentType.objects.get_for_model(model)

    return None


def flatten_dict(dikt, parent_key='', sep='__'):
    items = []
    for key, val in dikt.items():
        new_key = parent_key + sep + key if parent_key else key

        if isinstance(val, MutableMapping):
            items.extend(flatten_dict(val, new_key, sep=sep).items())
        # elif isinstance(val, list):
        #     for i, _v in enumerate(val, start=0):
        #         if isinstance(_v, dict):
        #             items.extend(flatten_dict(_v, "{}__{}".format(key, i)).items())
        #         else:
        #             items.append(("{}__{}".format(key, i), str(_v)))
        else:
            items.append((new_key, str(val)))
    return dict(items)


def get_bulk_import_celery_once_lock_key(async_result):
    result_args = async_result.args
    args = [('to_import', result_args[0]), ('username', result_args[1]), ('update_if_exists', result_args[2])]

    if async_result.name == 'core.common.tasks.bulk_import_parallel_inline':
        args.append(('threads', result_args[3]))

    return get_celery_once_lock_key(async_result.name, args)


def get_celery_once_lock_key(name, args):
    return queue_once_key(name, OrderedDict(args), None)
