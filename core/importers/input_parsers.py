import csv
import io
from zipfile import ZipFile

import requests
from ocldev.oclcsvtojsonconverter import OclStandardCsvToJsonConverter
from pydash import get, compact

from core.common.utils import is_zip_file, is_csv_file


def csv_file_data_to_input_list(file_content):
    return [row for row in csv.DictReader(io.StringIO(file_content))]  # pylint: disable=unnecessary-comprehension


class ImportContentParser:
    """
    1. Processes json data from 'content' arg
    2. Processes json/csv/zip file url from 'file_url' arg
    3. Processes json/csv/zip file from 'file' arg
    """
    def __init__(self, content=None, file_url=None, file=None):
        self.content = content
        self.file_url = file_url
        self.file = file
        self.file_name = get(self, 'file.name') if self.file else None
        self.errors = []
        self.extracted_file = None
        self.is_zip_file = False
        self.is_csv_file = False
        self.is_json_file = False

    def parse(self):
        self.validate_args()
        self.set_content_type()
        self.set_content_from_file()
        if not self.errors and not self.content:
            self.errors.append('Invalid input.')

    def validate_args(self):
        if len(compact([self.content, self.file_url, self.file])) != 1:
            self.errors.append('Invalid input.')

    def set_content_type(self):
        self.is_json_file = bool(self.content)
        self.is_zip_file = is_zip_file(name=self.file_name or self.file_url)
        self.is_csv_file = is_csv_file(name=self.file_name or self.file_url)

    def set_content_from_file(self):
        if self.file:
            self.file_name = get(self, 'file.name')
        elif self.file_url:
            self.set_file_from_response(self.fetch_file_from_url())
        self.set_content()

    def fetch_file_from_url(self):
        try:
            headers = {
                'User-Agent': 'OCL'  # user-agent required by mod_security on some servers
            }
            return requests.get(self.file_url, headers=headers, stream=True, timeout=30)
        except Exception as e:
            self.errors.append(f'Failed to download file from {self.file_url}, Exception: {e}.')

    def set_file_from_response(self, response):
        if get(response, 'ok'):
            if self.is_zip_file:
                self.file = io.BytesIO(response.content)
            else:
                self.file = response.text
        elif response:
            self.errors.append(f'Failed to download file from {self.file_url}, Status: {response.status_code}.')

    def set_content(self):
        if self.file:
            if self.is_zip_file:
                self.set_zipped_content()
            else:
                self.content = self.file.read()
                if isinstance(self.content, bytes):
                    self.content = self.content.decode('utf-8')
                if self.is_csv_file:
                    self.set_csv_content()

    def set_csv_content(self):
        try:
            self.content = OclStandardCsvToJsonConverter(
                input_list=csv_file_data_to_input_list(self.content),
                allow_special_characters=True
            ).process()
        except Exception as e:
            self.errors.append(f'Failed to process CSV file: {e}.')

    def set_zipped_content(self):
        with ZipFile(self.file, 'r') as zip_file:
            filename_list = zip_file.namelist()
            if len(filename_list) != 1:
                self.errors.append('Zip file must contain exactly one file.')
            else:
                with zip_file.open(filename_list[0]) as file:
                    self.extracted_file = file
                    self.content = file.read().decode('utf-8')
                    if is_csv_file(name=filename_list[0]):
                        self.set_csv_content()
