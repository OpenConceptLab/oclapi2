import os
import tempfile
import zipfile

from dateutil import parser
from django.urls import NoReverseMatch, reverse, get_resolver
from djqscsv import csv_file_for
from pydash import flatten

from core.common.constants import UPDATED_SINCE_PARAM
from core.common.services import S3


def cd_temp():
    cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)
    return cwd


def write_csv_to_s3(data, is_owner, **kwargs):
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


def get_downloads_path(is_owner):
    return 'downloads/creator/' if is_owner else 'downloads/reader/'


def get_csv_from_s3(filename, is_owner):
    filename = get_downloads_path(is_owner) + filename + '.csv.zip'
    return S3.url_for(filename)


def get_owner_type(owner, resources_url):
    resources_url_part = getattr(owner, resources_url, '').split('/')[1]
    return 'user' if resources_url_part == 'users' else 'org'


def join_uris(resources):
    return ', '.join([resource.uri for resource in resources])


def reverse_resource(resource, viewname, args=None, kwargs=None, **extra):
    """
    Generate the URL for the view specified as viewname of the object specified as resource.
    """
    kwargs = kwargs or {}
    parent = resource
    while parent is not None:
        if not hasattr(parent, 'get_url_kwarg'):
            return NoReverseMatch('Cannot get URL kwarg for %s' % resource)

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


def parse_updated_since_param(request):
    updated_since = request.query_params.get(UPDATED_SINCE_PARAM)
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
    return None
