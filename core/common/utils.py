import os
import tempfile
import zipfile

from boto.s3.connection import S3Connection
from django.conf import settings
from djqscsv import csv_file_for

from core.common.services import S3


class S3ConnectionFactory:
    s3_connection = None

    @classmethod
    def get_s3_connection(cls):
        if not cls.s3_connection:
            cls.s3_connection = S3Connection(settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY)
        return cls.s3_connection

    @classmethod
    def get_export_bucket(cls):
        conn = cls.get_s3_connection()
        return conn.get_bucket(settings.AWS_STORAGE_BUCKET_NAME)


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


def get_downloads_path(is_owner):
    return 'downloads/creator/' if is_owner else 'downloads/reader/'


def get_csv_from_s3(filename, is_owner):
    filename = get_downloads_path(is_owner) + filename + '.csv.zip'
    return S3.url_for(filename)


def add_user_to_org(userprofile, organization):
    transaction_complete = False
    if not organization.is_member(userprofile):
        try:
            userprofile.organizations.add(organization)
            transaction_complete = True
        finally:
            if not transaction_complete:
                userprofile.organizations.remove(organization)


def remove_user_from_org(userprofile, organization):
    transaction_complete = False
    if organization.is_member(userprofile):
        try:
            userprofile.organizations.remove(organization)
            transaction_complete = True
        finally:
            if not transaction_complete:
                userprofile.organizations.add(organization)


def get_owner_type(owner, resources_url):
    resources_url_part = getattr(owner, resources_url, '').split('/')[1]
    return 'user' if resources_url_part == 'users' else 'org'


def join_uris(resources):
    return ', '.join([resource.uri for resource in resources])
