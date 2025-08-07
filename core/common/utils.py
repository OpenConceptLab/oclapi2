# pylint: disable=cyclic-import # only occurring in dev env
import hashlib
import json
import mimetypes
import os
import random
import shutil
import tempfile
import time
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import MutableMapping  # pylint: disable=no-name-in-module,deprecated-class
from datetime import timedelta
from threading import local
from urllib import parse

import requests
from celery_once import AlreadyQueued
from celery_once.helpers import queue_once_key
from dateutil import parser
from django.conf import settings
from django.urls import NoReverseMatch, reverse, get_resolver
from django.utils import timezone
from djqscsv import csv_file_for
from pydash import flatten, compact, get
from requests import ConnectTimeout
from requests.auth import HTTPBasicAuth
from rest_framework.utils import encoders

from core.common.constants import UPDATED_SINCE_PARAM, BULK_IMPORT_QUEUES_COUNT, CURRENT_USER, REQUEST_URL, \
    TEMP_PREFIX
from core.settings import EXPORT_SERVICE


def get_latest_dir_in_path(path):
    all_sub_dirs = [path + d for d in os.listdir(path) if os.path.isdir(path + d)]
    return max(all_sub_dirs, key=os.path.getmtime)


def cd_temp():
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
    get_export_service().upload_file(
        key=key, file_path=os.path.abspath(zip_file.filename), binary=True,
        metadata={'ContentType': 'application/zip'}, headers={'content-type': 'application/zip'}
    )
    os.chdir(cwd)
    return get_export_service().url_for(key)


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

    export_service = get_export_service()
    if export_service.exists(filename):
        return export_service.url_for(filename)

    return None


def reverse_resource(resource, viewname, args=None, kwargs=None, **extra):
    """
    Generate the URL for the view specified as viewname of the object specified as resource.
    """
    kwargs = kwargs or {}
    parent = resource
    while parent is not None:
        if not hasattr(parent, 'get_url_kwarg'):
            return NoReverseMatch(f'Cannot get URL kwarg for {resource}')  # pragma: no cover

        if parent.is_versioned and not parent.is_head:
            from core.collections.models import Collection
            from core.sources.models import Source
            if isinstance(parent, (Source, Collection)):
                head = parent.get_latest_version()
            else:
                head = parent.head

            if head:
                kwargs.update({head.get_url_kwarg(): head.mnemonic})

            version = parent.version if is_url_encoded_string(
                parent.version) else encode_string(parent.version, safe=' ')

            kwargs.update({parent.get_url_kwarg(): version})
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
    version = resource.version if is_url_encoded_string(resource.version) else encode_string(resource.version, safe=' ')
    kwargs.update({resource.get_url_kwarg(): version})
    resource_url_kwarg = resource.get_resource_url_kwarg()

    if resource_url_kwarg not in kwargs:
        kwargs[resource_url_kwarg] = resource.mnemonic

    return reverse_resource(resource, viewname, args, kwargs, **extra)


def get_kwargs_for_view(view_name):
    resolver = get_resolver()
    patterns = resolver.reverse_dict.getlist(view_name)
    return list(set(flatten([p[0][0][1] for p in patterns])))


def parse_updated_since_param(params):
    return from_string_to_date(params.get(UPDATED_SINCE_PARAM))


def from_string_to_date(date_string):  # pylint: disable=inconsistent-return-statements
    if not isinstance(date_string, str):
        return date_string
    if date_string:
        try:
            return parser.parse(date_string)
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
        return {}


def get_class(kls):
    parts = kls.split('.')
    module = ".".join(parts[:-1])
    _module = __import__(module)
    for comp in parts[1:]:
        _module = getattr(_module, comp)
    return _module


def write_export_file(
        version, resource_type, resource_serializer_type, logger, start_time
):  # pylint: disable=too-many-statements,too-many-locals,too-many-branches
    from core.concepts.models import Concept
    from core.mappings.models import Mapping
    cwd = cd_temp()
    logger.info(f'Writing export file to tmp directory: {cwd}')

    logger.info(f'Found {resource_type} version {version.version}.  Looking up resource...')
    logger.info(f'Found {resource_type} {version.mnemonic}.  Serializing attributes...')

    resource_serializer = get_class(resource_serializer_type)(version)
    data = resource_serializer.data
    resource_string = json.dumps(data, cls=encoders.JSONEncoder)
    logger.info('Done serializing attributes.')

    batch_size = 100
    is_collection = resource_type == 'collection'

    concepts_qs = Concept.objects.none()
    mappings_qs = Mapping.objects.none()

    if is_collection:
        if version.expansion_uri:
            concepts_qs = Concept.expansion_set.through.objects.filter(expansion_id=version.expansion.id)
            mappings_qs = Mapping.expansion_set.through.objects.filter(expansion_id=version.expansion.id)
    else:
        concepts_qs = Concept.sources.through.objects.filter(source_id=version.id)
        mappings_qs = Mapping.sources.through.objects.filter(source_id=version.id)

    filters = {}

    if not is_collection:
        filters['is_active'] = True
        if version.is_head:
            filters['is_latest_version'] = True

    with open('export.json', 'w') as out:
        out.write(f'{resource_string[:-1]}, "concepts": [')

    resource_name = resource_type.title()

    if concepts_qs.exists():
        logger.info(f'{resource_name} has concepts. Getting them in batches of {batch_size:d}...')
        concept_serializer_class = get_class('core.concepts.serializers.ConceptVersionExportSerializer')
        start = 0
        end = batch_size
        batch_queryset = concepts_qs.order_by('-concept_id')[start:end]

        while batch_queryset.exists():
            logger.info(f'Serializing concepts {start + 1:d} - {end:d}...')
            queryset = Concept.objects.filter(
                id__in=batch_queryset.values_list('concept_id')).filter(**filters).order_by('-id')
            if queryset.exists():
                if start > 0:
                    with open('export.json', 'a') as out:
                        out.write(', ')
                concept_versions = queryset.prefetch_related('names', 'descriptions')
                data = concept_serializer_class(concept_versions, many=True).data
                concept_string = json.dumps(data, cls=encoders.JSONEncoder)
                concept_string = concept_string[1:-1]

                with open('export.json', 'a') as out:
                    out.write(concept_string)

            start += batch_size
            end += batch_size
            batch_queryset = concepts_qs.order_by('-concept_id')[start:end]

        logger.info('Done serializing concepts.')

    if is_collection:
        references_qs = version.references
        total_references = references_qs.count()

        with open('export.json', 'a') as out:
            out.write('], "references": [')
        if total_references:
            logger.info(
                f'{resource_name} has {total_references:d} references. Getting them in batches of {batch_size:d}...'
            )
            reference_serializer_class = get_class('core.collections.serializers.CollectionReferenceDetailSerializer')
            for start in range(0, total_references, batch_size):
                end = min(start + batch_size, total_references)
                logger.info(f'Serializing references {start + 1:d} - {end:d}...')
                references = references_qs.order_by('-id').filter()[start:end]
                reference_serializer = reference_serializer_class(references, many=True)
                reference_string = json.dumps(reference_serializer.data, cls=encoders.JSONEncoder)
                reference_string = reference_string[1:-1]
                with open('export.json', 'a') as out:
                    out.write(reference_string)
                    if end != total_references:
                        out.write(', ')
            logger.info('Done serializing references.')

    with open('export.json', 'a') as out:
        out.write('], "mappings": [')

    if mappings_qs.exists():
        logger.info(f'{resource_name} has mappings. Getting them in batches of {batch_size:d}...')
        mapping_serializer_class = get_class('core.mappings.serializers.MappingDetailSerializer')
        start = 0
        end = batch_size
        batch_queryset = mappings_qs.order_by('-mapping_id')[start:end]

        while batch_queryset.exists():
            logger.info(f'Serializing mappings {start + 1:d} - {start + batch_size:d}...')
            queryset = Mapping.objects.filter(
                id__in=batch_queryset.values_list('mapping_id')).filter(**filters).order_by('-id')
            if queryset.exists():
                if start > 0:
                    with open('export.json', 'a') as out:
                        out.write(', ')

                data = mapping_serializer_class(queryset, many=True).data
                mapping_string = json.dumps(data, cls=encoders.JSONEncoder)
                mapping_string = mapping_string[1:-1]
                with open('export.json', 'a') as out:
                    out.write(mapping_string)

            start += batch_size
            end += batch_size
            batch_queryset = mappings_qs.order_by('-mapping_id')[start:end]

        logger.info('Done serializing mappings.')

    end_time = str(round((time.time() - start_time) + 2, 2))
    with open('export.json', 'a') as out:
        out.write('], "export_time": ' + json.dumps(f"{end_time}secs", cls=encoders.JSONEncoder) + '}')

    version.update_extras('__export_time', end_time)

    with zipfile.ZipFile('export.zip', 'w', zipfile.ZIP_DEFLATED) as _zip:
        _zip.write('export.json')

    file_path = os.path.abspath('export.zip')
    logger.info(file_path)
    logger.info('Done compressing.  Uploading...')

    s3_key = version.version_export_path
    export_service = get_export_service()
    if version.is_head:
        export_service.delete_objects(version.get_version_export_path(suffix=None))

    upload_status_code = export_service.upload_file(
        key=s3_key, file_path=file_path, binary=True, metadata={'ContentType': 'application/zip'},
        headers={'content-type': 'application/zip'}
    )
    logger.info(f'Upload response status: {str(upload_status_code)}')
    uploaded_path = export_service.url_for(s3_key)
    logger.info(f'Uploaded to {uploaded_path}.')

    if not get(settings, 'TEST_MODE', False):
        tmp_dir_path = file_path.replace('/export.zip', '')
        logger.info(f'Removing tmp {tmp_dir_path}.')
        shutil.rmtree(tmp_dir_path, ignore_errors=True)

    os.chdir(cwd)


def get_api_base_url():
    return settings.API_BASE_URL


def to_snake_case(string):
    # from https://www.geeksforgeeks.org/python-program-to-convert-camel-case-string-to-snake-case/
    return ''.join(['_' + i.lower() if i.isupper() else i for i in string]).lstrip('_')


def to_camel_case(string):
    # from https://www.geeksforgeeks.org/python-convert-snake-case-string-to-camel-case/?ref=rp
    temp = string.split('_')
    return str(temp[0] + ''.join(ele.title() for ele in temp[1:]))


def flower_get(url, **kwargs):
    """
    Returns a flower response from the given endpoint url.
    :param url:
    :return:
    """
    return requests.get(
        f'http://{settings.FLOWER_HOST}:{settings.FLOWER_PORT}/{url}',
        auth=HTTPBasicAuth(settings.FLOWER_USER, settings.FLOWER_PASSWORD),
        **kwargs
    )


def es_get(url, **kwargs):
    """
    Returns an es response from the given endpoint url.
    :param url:
    :return:
    """
    auth = None
    if settings.ES_USER and settings.ES_PASSWORD:
        auth = HTTPBasicAuth(settings.ES_USER, settings.ES_PASSWORD)

    if settings.ES_HOSTS:
        for es_host in settings.ES_HOSTS.split(','):
            try:
                return requests.get(
                    f'{settings.ES_SCHEME}://{es_host}/{url}', auth=auth,
                    **kwargs
                )
            except ConnectTimeout:
                continue
    else:
        return requests.get(
            f'{settings.ES_SCHEME}://{settings.ES_HOST}:{settings.ES_PORT}/{url}', auth=auth,
            **kwargs
        )

    return None


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

    from core.common.tasks import bulk_import_parts_inline, bulk_import_parallel_inline, bulk_import_inline, bulk_import
    args = (to_import, username, update_if_exists)
    if inline:
        if sub_task:
            task_func = bulk_import_parts_inline
        elif threads:
            task_func = bulk_import_parallel_inline
            args = (to_import, username, update_if_exists, threads)
        else:
            task_func = bulk_import_inline
    else:
        task_func = bulk_import
    task = get_queue_task_names(import_queue, username, name=task_func.__name__)

    try:
        task_func.apply_async(args, task_id=task.id, queue=task.queue)
    except AlreadyQueued as ex:
        if task:
            task.delete()
        raise ex

    return task


def get_queue_task_names(import_queue, username, **kwargs):
    if username in ['root', 'ocladmin'] and import_queue != 'concurrent':
        queue_id = 'bulk_import_root'
    elif import_queue == 'concurrent':
        queue_id = import_queue
    elif import_queue:
        # assigning to one of 4 queues processed in order
        hash_value = hashlib.sha256((username + import_queue).encode()).hexdigest()
        queue_id = 'bulk_import_' + str(int(hash_value[:2], 16) % BULK_IMPORT_QUEUES_COUNT)
    else:
        # assigning randomly to one of 4 queues processed in order
        queue_id = 'bulk_import_' + str(random.randrange(0, BULK_IMPORT_QUEUES_COUNT))

    from core.tasks.models import Task
    return Task.new(queue=queue_id, username=username, import_queue=import_queue, **kwargs)


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


def to_owner_uri(expression):
    return '/' + '/'.join(compact(expression.split('/'))[:2]) + '/'


def separate_version(expression):
    versionless_expression = drop_version(expression)
    if expression != versionless_expression:
        return expression.replace(versionless_expression, '').replace('/', ''), versionless_expression

    return None, expression


def canonical_url_to_url_and_version(canonical_url):
    version = None
    if '|' in canonical_url:
        canonical_url, version = canonical_url.split('|')

    return canonical_url, version


def generate_temp_version():
    return f"{TEMP_PREFIX}{str(uuid.uuid4())[:8]}"


def startswith_temp_version(value):
    return value.startswith(TEMP_PREFIX)


def jsonify_safe(value):
    if isinstance(value, dict):
        return value

    try:
        return json.loads(value)
    except:  # pylint: disable=bare-except
        return value


def web_url():
    url = settings.WEB_URL
    if url:
        return url
    env = settings.ENV
    if not env or env in ['development', 'ci']:
        return 'http://localhost:4000'

    if env == 'production':
        return "https://app.openconceptlab.org"

    return f"https://app.{env}.openconceptlab.org"


def get_resource_class_from_resource_name(resource):  # pylint: disable=too-many-return-statements
    if not resource:
        return resource

    name = resource.lower()
    if 'concept' in name:
        from core.concepts.models import Concept
        return Concept
    if 'mapping' in name:
        from core.mappings.models import Mapping
        return Mapping
    if 'user' in name:
        from core.users.models import UserProfile
        return UserProfile
    if 'org' in name:
        from core.orgs.models import Organization
        return Organization
    if 'source' in name:
        from core.sources.models import Source
        return Source
    if 'collection' in name and 'reference' not in name and 'expansion' not in name:
        from core.collections.models import Collection
        return Collection
    if 'reference' in name:
        from core.collections.models import CollectionReference
        return CollectionReference
    if 'expansion' in name:
        from core.collections.models import Expansion
        return Expansion

    return None


def get_resource_class_from_resource_uri(uri):  # pylint: disable=too-many-return-statements
    if not uri:
        return uri

    name = uri.lower()
    if '/concepts/' in name:
        from core.concepts.models import Concept
        return Concept
    if '/mappings/' in name:
        from core.mappings.models import Mapping
        return Mapping
    if '/expansions/' in name:
        from core.collections.models import Expansion
        return Expansion
    if '/references/' in name:
        from core.collections.models import CollectionReference
        return CollectionReference
    if '/sources/' in name:
        from core.sources.models import Source
        return Source
    if '/collections/' in name:
        from core.collections.models import Collection
        return Collection
    if name.startswith('/users/'):
        from core.users.models import UserProfile
        return UserProfile
    if name.startswith('/orgs/'):
        from core.orgs.models import Organization
        return Organization

    return None


def get_content_type_from_resource_name(resource):
    content_type = None

    model = get_resource_class_from_resource_name(resource)
    if model:
        from django.contrib.contenttypes.models import ContentType
        content_type = ContentType.objects.get_for_model(model)

    return content_type


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
            _val = str(val).replace('-', '_')
            items.append((new_key, _val))
    return dict(items)


def get_bulk_import_celery_once_lock_key(async_result):
    result_args = async_result.args
    if not result_args:
        return None
    args = [('to_import', result_args[0]), ('username', result_args[1]), ('update_if_exists', result_args[2])]

    if async_result.name == 'core.common.tasks.bulk_import_parallel_inline':
        args.append(('threads', result_args[3]))

    return get_celery_once_lock_key(async_result.name, args)


def get_celery_once_lock_key(name, args):
    return queue_once_key(name, OrderedDict(args), None)


def guess_extension(file=None, name=None):
    extension = None
    if file or name:
        if file:
            name = file.name
        _, extension = os.path.splitext(name)

        if not extension:
            extension = mimetypes.guess_extension(name)
    return extension


def is_csv_file(file=None, name=None):
    return is_file_extension('csv', file, name)


def is_zip_file(file=None, name=None):
    return is_file_extension('zip', file, name)


def is_file_extension(extension, file=None, name=None):
    if not file and not name:
        return False

    file_extension = guess_extension(file=file, name=name)

    return file_extension and file_extension.endswith(extension)


def is_url_encoded_string(string, lower=True):
    encoded_string = encode_string(decode_string(string), safe=' ')

    if lower:
        return string.lower() == encoded_string.lower()

    return string == encoded_string


def decode_string(string, plus=True):
    return parse.unquote_plus(string) if plus else parse.unquote(string)


def encode_string(string, **kwargs):
    return parse.quote(string, **kwargs)


def to_parent_uri_from_kwargs(params):
    if not params:
        return None

    owner_type, owner, parent_type, parent = None, None, None, None

    if 'org' in params:
        owner_type = 'orgs'
        owner = params.get('org')
    elif 'user' in params:
        owner_type = 'users'
        owner = params.get('user')

    if 'source' in params:
        parent_type = 'sources'
        parent = params.get('source')
    elif 'collection' in params:
        parent_type = 'collections'
        parent = params.get('collection')

    return '/' + '/'.join(compact([owner_type, owner, parent_type, parent, params.get('version', None)])) + '/'


def api_get(url, user, **kwargs):
    response = requests.get(
        settings.API_BASE_URL + url, headers=user.auth_headers,
        **kwargs
    )
    return response.json()


thread_locals = local()


def set_current_user(func):
    setattr(thread_locals, CURRENT_USER, func.__get__(func, local))  # pylint: disable=unnecessary-dunder-call


def set_request_url(func):
    setattr(thread_locals, REQUEST_URL, func.__get__(func, local))  # pylint: disable=unnecessary-dunder-call


def get_current_user():
    current_user = getattr(thread_locals, CURRENT_USER, None)
    if callable(current_user):
        current_user = current_user()  # pylint: disable=not-callable

    return current_user


def get_current_authorized_user():
    user = get_current_user()
    return user if user and user.is_authenticated else None


def get_request_url():
    request_url = getattr(thread_locals, REQUEST_URL, None)
    if callable(request_url):
        request_url = request_url()  # pylint: disable=not-callable

    return request_url


def nested_dict_values(_dict):
    for value in _dict.values():
        if isinstance(value, dict):
            yield from nested_dict_values(value)
        else:
            yield value


def chunks(lst, size):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def es_id_in(search, ids):
    if ids:
        return search.query("terms", _id=ids)
    return search


def es_to_pks(search):
    # doesn't care about the order
    default_limit = 25
    limit = default_limit
    offset = 0
    result_count = 25
    pks = []
    while result_count > 0:
        hits = search[offset:limit].execute().hits
        result_count = len(hits)
        if result_count:
            pks += [hit.meta.id for hit in hits]
        offset += default_limit
        limit += default_limit
    return pks


def batch_qs(qs, batch_size=1000):
    """
    Returns a sub-queryset for each batch in the given queryset.

    Usage:
        # Make sure to order your querset
        article_qs = Article.objects.order_by('id')
        for qs in batch_qs(article_qs):
            for article in qs:
                print article.body
    """
    total = qs.count()
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        yield qs[start:end]


def split_list_by_condition(items, predicate):
    include, exclude = [], []
    for item in items:
        (exclude, include)[predicate(item)].append(item)

    return include, exclude


def is_canonical_uri(string):
    return ':' in string


def get_export_service():
    parts = EXPORT_SERVICE.split('.')
    klass = parts[-1]
    mod = __import__('.'.join(parts[0:-1]), fromlist=[klass])
    return getattr(mod, klass)()


def get_start_of_month(date=timezone.now().date()):
    return date.replace(day=1)


def get_end_of_month(date=timezone.now().date()):
    next_month = date.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)


def get_prev_month(date=timezone.now().date()):
    return date.replace(day=1) - timedelta(days=1)


def to_int(value, default_value):
    try:
        return int(value) or default_value
    except (ValueError, TypeError):
        return default_value


def generic_sort(_list):
    def compare(item):
        if isinstance(item, (int, float, str, bool)):
            return item
        return str(item)
    return sorted(_list, key=compare)


def get_falsy_values():
    return ['false', False, 'False', 0, '0', 'None', 'null']


def get_truthy_values():
    return ['true', True, 'True', 1, '1']


def get_date_range_label(start_date, end_date):
    start = from_string_to_date(start_date)
    end = from_string_to_date(end_date)

    start_month = start.strftime('%B')
    end_month = end.strftime('%B')

    if start.year == end.year:
        if start_month == end_month:
            return f"{start.day:02d} - {end.day:02d} {start_month} {start.year}"
        return f"{start.day:02d} {start_month} - {end.day:02d} {end_month} {start.year}"

    return f"{start.day:02d} {start_month} {start.year} - {end.day:02d} {end_month} {end.year}"


def format_url_for_search(url):
    if url:
        return url.replace('/', '_').replace(':', '_')
    return url


def clean_term(term):
    return term.lower().replace(' ', '').replace('-', '').replace('_', '')


def get_embeddings(txt):
    from core.toggles.models import Toggle
    if not Toggle.get('SEMANTIC_SEARCH_TOGGLE') or settings.ENV == 'ci':
        return None

    model = settings.LM
    if not model:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(settings.LM_MODEL_NAME)
    return model.encode(str(txt))
