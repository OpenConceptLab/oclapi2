import tempfile
import zipfile
import os

from boto.s3.key import Key
from boto.s3.connection import S3Connection
from djqscsv import csv_file_for
from django.conf import settings


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

    bucket = S3ConnectionFactory.get_export_bucket()
    k = Key(bucket)
    _dir = 'downloads/creator/' if is_owner else 'downloads/reader/'
    k.key = _dir + zip_file_name
    k.set_contents_from_filename(zip_file_name)

    os.chdir(cwd)
    return bucket.get_key(k.key).generate_url(expires_in=60)


def get_csv_from_s3(filename, is_owner):
    _dir = 'downloads/creator' if is_owner else 'downloads/reader'
    filename = _dir + filename + '.csv.zip'
    bucket = S3ConnectionFactory.get_export_bucket()
    key = bucket.get_key(filename)
    return key.generate_url(expires_in=600) if key else None


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
