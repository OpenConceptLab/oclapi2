import json
import os
import urllib

from core.common.tests import OCLAPITestCase
from core.integration_tests import test_utils
from core.orgs.models import Organization
from core.users.models import UserProfile

'''
Test resources taken from https://terminology.hl7.org/package.tgz,
which have been extracted to the fhir_hl7 directory.
'''


class HL7FhirTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.maxDiff = None

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

    def test_fhir_hl7(self):
        module_path = os.path.dirname(__file__)
        fhir_hl7_path = os.path.join(module_path, 'fhir_hl7')
        fhir_hl7 = os.listdir(fhir_hl7_path)

        print()
        total_count = 0
        errors_count = 0
        errors = []

        offset = int(os.getenv('OFFSET', 0))

        for test_file in fhir_hl7:
            if test_file.startswith('CodeSystem'):
                print(f'Importing {total_count} file: {test_file}')
                total_count = total_count + 1
                if offset > total_count:
                    continue
                url = f"/orgs/{self.organization.mnemonic}/CodeSystem/"
                json_file = test_utils.load_json(test_file, 'fhir_hl7')

                # Errors due to names being too long for b-tee index
                if test_file.endswith('conceptdomains.json'):
                    errors_count = errors_count + 1
                    print(f'Errored {errors_count} times out of {total_count}')
                    continue

                response = self.post(url, json_file)
                json_response = response.json()
                # self.assertEqual(response.status_code, 201, json_response)
                # self.adjust_to_ocl_format(json_file)
                # test_utils.ignore_json_paths(json_file, json_response, ['concept[*].designation', 'date',
                #                                         'identifier', 'meta', 'text', 'revisionDate', 'count'])
                # self.assertJSONEqual(json.dumps(json_response), json_file)

                # Errors due to canonical URL not accepting URI
                if response.status_code != 201:
                    errors.append(json_response)
                    errors_count = errors_count + 1
                    print(f'Errored with: {json_response}')
                    print(f'Errored {errors_count} times out of {total_count}')

        self.assertEqual(errors_count, 0, f'Errored {errors_count} times out of {total_count}. Errors: {errors}')
