import json
import os
import urllib.parse
from jsonpath_ng import parse

from core.collections.models import Collection
from core.common.tests import OCLAPITestCase
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class ValueSetsTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.maxDiff = None

    @staticmethod
    def load_json(test_file):
        module_dir = os.path.dirname(__file__)  # get current directory
        file_path = os.path.join(module_dir, test_file)
        with open(file_path) as f:
            json_file = json.load(f)
        return json_file

    def post(self, url, json_body):
        return self.client.post(
            url,
            json_body,
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

    def delete(self, url):
        return self.client.delete(url, HTTP_AUTHORIZATION='Token ' + self.token,
                                  format='json')

    @staticmethod
    def update(json_file, path, value):
        jsonpath_expr = parse(path)
        jsonpath_expr.update_or_create(json_file, value)

    @staticmethod
    def find(json_file, path):
        jsonpath_expr = parse(path)
        return jsonpath_expr.find(json_file)

    def adjust_to_ocl_format(self, json_file):
        concepts = json_file.get('concept', [])
        for concept in concepts:
            code = concept.get('code', None)
            if code:
                code = urllib.parse.quote(code, safe=' ')
                concept.update({'code': code})
        json_file.update({'concept': concepts})

        self.update(json_file, 'concept[*].property', [
            {'code': 'conceptclass', 'value': 'Misc'},
            {'code': 'datatype', 'value': 'N/A'}
        ])
        if 'jurisdiction' not in json_file:
            self.update(json_file, 'jurisdiction', {})
        self.update(json_file, 'language', 'en')
        self.update(json_file, 'property', [
            {
                'code': 'conceptclass',
                'description': 'Standard list of concept classes.',
                'type': 'string',
                'uri': 'http://localhost:8000/orgs/OCL/sources/Classes/concepts'
            }, {
                'code': 'datatype',
                'description': 'Standard list of concept datatypes.',
                'uri': 'http://localhost:8000/orgs/OCL/sources/Datatypes/concepts',
                'type': 'string',
            }, {
                'code': 'inactive',
                'description': 'True if the concept is not considered active.',
                'type': 'coding',
                'uri': 'http://hl7.org/fhir/concept-properties'
            }
        ])


    def ignore_paths(self, json_file, json_response, paths):
        for path in paths:
            self.update(json_file, path, None)
            self.update(json_response, path, None)

        for concept in json_response.get('concept', []):
            if not concept.get('definition', None):
                concept.pop('definition')

    @staticmethod
    def remove_duplicate_codes(json_file):
        concepts = json_file.get('concept', [])
        unique_concepts = []
        codes = set()
        for concept in concepts:
            if concept.get('code') not in codes:
                codes.add(concept.get('code'))
                unique_concepts.append(concept)
        json_file.update({'concept': unique_concepts})

    def test_posting_value_sets(self):
        test_files = ['value_sets/value_sets_who_core_payment.json']
        #               'value_sets/value_sets_who_core_contraceptive.json',
        #               'value_sets/value_sets_who_core_hiv.json',
        #               'value_sets/value_sets_who_core_education.json']
        print()

        for test_file in test_files:
            print('Testing ' + test_file)
            url = f"/orgs/{self.organization.mnemonic}/CodeSystem/"
            json_file = self.load_json('code_systems/code_systems_who_core.json')
            self.remove_duplicate_codes(json_file)
            json_response = self.post(url, json_file)
            source_url = json_response.get('url')

            url = f"/orgs/{self.organization.mnemonic}/ValueSet/"
            json_file = self.load_json(test_file)
            response = self.post(url, json_file)
            json_response = response.json()

            self.ignore_paths(json_file, json_response, ['jurisdiction', 'date', 'copyright', 'purpose',
                                                         'identifier', 'meta', 'text', 'revisionDate', 'count',
                                                         'compose.include[*].concept[*].designation',
                                                         'compose.inactive', 'compose.lockedDate'])

            self.assertJSONEqual(json.dumps(json_response), json_file)
            self.assertEqual(response.status_code, 201)

            Source.objects.filter(canonical_url=source_url).delete()
            Collection.objects.filter(canonical_url=json_response.get('url')).delete()
