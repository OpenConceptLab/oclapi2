import json
import os
import urllib.parse
from jsonpath_ng import parse
from mock.mock import patch, Mock, ANY

from core.common.tests import OCLAPITestCase
from core.orgs.models import Organization
from core.sources.models import Source
from core.users.models import UserProfile


class CodeSystemsTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.user = UserProfile.objects.filter(is_superuser=True).first()
        self.token = self.user.get_token()
        self.maxDiff = None

    @staticmethod
    def load_json(test_file):
        module_dir = os.path.dirname(__file__)  # get current directory
        file_path = os.path.join(module_dir, 'code_systems', test_file)
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
            {'code': 'conceptclass', 'valueString': 'Misc'},
            {'code': 'datatype', 'valueString': 'N/A'}
        ])
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
                'type': 'Coding',
                'uri': 'http://hl7.org/fhir/concept-properties'
            }
        ])

    def ignore_paths(self, json_file, json_response, paths):
        for path in paths:
            self.update(json_file, path, None)
            self.update(json_response, path, None)

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

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    def test_posting_code_systems(self):
        test_files = ['code_systems_who_core.json', 'code_systems_who_fp.json', 'code_systems_who_sti.json',
                      'code_systems_who_ddcc_category_codes.json']
        for test_file in test_files:
            print('Testing ' + test_file)
            url = f"/orgs/{self.organization.mnemonic}/CodeSystem/"
            json_file = self.load_json(test_file)

            self.remove_duplicate_codes(json_file)

            response = self.post(url, json_file)
            json_response = response.json()

            self.adjust_to_ocl_format(json_file)
            self.ignore_paths(json_file, json_response, ['concept[*].designation', 'date',
                                                         'identifier', 'meta', 'text', 'revisionDate', 'count'])
            self.assertJSONEqual(json.dumps(json_response), json_file)
            self.assertEqual(response.status_code, 201)

            Source.objects.filter(canonical_url=json_response.get('url')).delete()

    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    def test_posting_code_systems_specific_json(self):
        response = self.post(
            f"/orgs/{self.organization.mnemonic}/CodeSystem/",
            {
                "resourceType": "CodeSystem",
                "id": "presentOnAdmission",
                "text": {
                    "status": "generated",
                    "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\"><p>This case-sensitive code system <code>https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding</code> defines the following codes:</p><table class=\"codes\"><tr><td style=\"white-space:nowrap\"><b>Code</b></td><td><b>Definition</b></td></tr><tr><td style=\"white-space:nowrap\">Y<a name=\"presentOnAdmission-Y\"> </a></td><td>Diagnosis was present at time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as &quot;Y&quot; for the POA Indicator.</td></tr><tr><td style=\"white-space:nowrap\">N<a name=\"presentOnAdmission-N\"> </a></td><td>Diagnosis was not present at time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;N&quot; for the POA Indicator.</td></tr><tr><td style=\"white-space:nowrap\">U<a name=\"presentOnAdmission-U\"> </a></td><td>Documentation insufficient to determine if the condition was present at the time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;U&quot; for the POA Indicator.</td></tr><tr><td style=\"white-space:nowrap\">W<a name=\"presentOnAdmission-W\"> </a></td><td>Clinically undetermined.  Provider unable to clinically determine whether the condition was present at the time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as &quot;W&quot; for the POA Indicator.</td></tr><tr><td style=\"white-space:nowrap\">1<a name=\"presentOnAdmission-1\"> </a></td><td>Unreported/Not used.  Exempt from POA reporting.  This code is equivalent to a blank on the UB-04, however; it was determined that blanks are undesirable when submitting this data via the 4010A. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;1&quot; for the POA Indicator. The \\u201c1\\u201d POA Indicator should not be applied to any codes on the HAC list.  For a complete list of codes on the POA exempt list, see  the Official Coding Guidelines for ICD-10-CM.</td></tr></table></div>"  # pylint: disable=line-too-long
                },
                "url": "https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding",
                "identifier": [{
                                   "system": "urn:ietf:rfc:3986",
                                   "value": "urn:oid:2.16.840.1.113883.6.301.11"
                               }],
                "version": "07/14/2020",
                "name": "PresentOnAdmission",
                "title": "CMS Present on Admission (POA) Indicator",
                "status": "active",
                "experimental": False,
                "date": "2021-06-24T00:00:00.000-07:00",
                "publisher": "Centers for Medicare & Medicaid Services",
                "contact": [{
                                "name": "Centers for Medicare & Medicaid Services; 7500 Security Boulevard, Baltimore, MD 21244,  USA"  # pylint: disable=line-too-long
                            }, {
                                "name": "Marilu Hue",
                                "telecom": [{
                                                "system": "email",
                                                "value": "marilu.hue@cms.hhs.gov"
                                            }]
                            }, {
                                "name": "James Poyer",
                                "telecom": [{
                                                "system": "email",
                                                "value": "james.poyer@cms.hhs.gov"
                                            }]
                            }],
                "description": "This code system consists of Present on Admission (POA) indicators which are assigned to the principal and secondary diagnoses (as defined in Section II of the Official Guidelines for Coding and Reporting) and the external cause of injury codes to indicate the presence or absence of the diagnosis at the time of inpatient admission.",  # pylint: disable=line-too-long
                "jurisdiction": [{
                                     "coding": [{
                                                    "system": "urn:iso:std:iso:3166",
                                                    "code": "US"
                                                }]
                                 }],
                "copyright": "The POA Indicator Codes are in the public domain and are free to use without restriction.",  # pylint: disable=line-too-long
                "caseSensitive": True,
                "compositional": False,
                "versionNeeded": False,
                "content": "complete",
                "count": 5,
                "concept": [{
                                "code": "Y",
                                "definition": "Diagnosis was present at time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as \"Y\" for the POA Indicator."  # pylint: disable=line-too-long
                            }, {
                                "code": "N",
                                "definition": "Diagnosis was not present at time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as \"N\" for the POA Indicator."  # pylint: disable=line-too-long
                            }, {
                                "code": "U",
                                "definition": "Documentation insufficient to determine if the condition was present at the time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as \"U\" for the POA Indicator."  # pylint: disable=line-too-long
                            }, {
                                "code": "W",
                                "definition": "Clinically undetermined.  Provider unable to clinically determine whether the condition was present at the time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as \"W\" for the POA Indicator."  # pylint: disable=line-too-long
                            }, {
                                "code": "1",
                                "definition": "Unreported/Not used.  Exempt from POA reporting.  This code is equivalent to a blank on the UB-04, however; it was determined that blanks are undesirable when submitting this data via the 4010A. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as \"1\" for the POA Indicator. The \\u201c1\\u201d POA Indicator should not be applied to any codes on the HAC list.  For a complete list of codes on the POA exempt list, see  the Official Coding Guidelines for ICD-10-CM."  # pylint: disable=line-too-long
                            }]
            }
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            json.loads(json.dumps(response.data)),
            {
                'resourceType': 'CodeSystem',
                'url': 'https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding',
                'title': 'CMS Present on Admission (POA) Indicator',
                'status': 'active',
                'id': 'presentOnAdmission',
                'language': 'en',
                'count': 5,
                'content': 'complete',
                'property': [{
                                 'code': 'conceptclass',
                                 'uri': ANY,
                                 'description': 'Standard list of concept classes.',
                                 'type': 'string'
                             }, {
                                 'code': 'datatype',
                                 'uri': ANY,
                                 'description': 'Standard list of concept datatypes.',
                                 'type': 'string'
                             }, {
                                 'code': 'inactive',
                                 'uri': 'http://hl7.org/fhir/concept-properties',
                                 'description': 'True if the concept is not considered active.',
                                 'type': 'Coding'
                             }],
                'meta': {
                    'lastUpdated': ANY
                },
                'version': '07/14/2020',
                'identifier': [{
                                   'system': 'urn:ietf:rfc:3986',
                                   'value': 'urn:oid:2.16.840.1.113883.6.301.11'
                               }, {
                                   'system': ANY,
                                   'value': '/orgs/OCL/CodeSystem/presentOnAdmission/',
                                   'type': {
                                       'text': 'Accession ID',
                                       'coding': [{
                                                      'system': 'http://hl7.org/fhir/v2/0203',
                                                      'code': 'ACSN',
                                                      'display': 'Accession ID'
                                                  }]
                                   }
                               }],
                'contact': [{
                                'name': 'Centers for Medicare & Medicaid Services; 7500 Security Boulevard, Baltimore, MD 21244,  USA'  # pylint: disable=line-too-long
                            }, {
                                'name': 'Marilu Hue',
                                'telecom': [{
                                                'value': 'marilu.hue@cms.hhs.gov',
                                                'system': 'email'
                                            }]
                            }, {
                                'name': 'James Poyer',
                                'telecom': [{
                                                'value': 'james.poyer@cms.hhs.gov',
                                                'system': 'email'
                                            }]
                            }],
                'jurisdiction': [{
                                     'coding': [{
                                                    'code': 'US',
                                                    'system': 'urn:iso:std:iso:3166'
                                                }]
                                 }],
                'name': 'PresentOnAdmission',
                'description': 'This code system consists of Present on Admission (POA) indicators which are assigned to the principal and secondary diagnoses (as defined in Section II of the Official Guidelines for Coding and Reporting) and the external cause of injury codes to indicate the presence or absence of the diagnosis at the time of inpatient admission.',  # pylint: disable=line-too-long
                'publisher': 'Centers for Medicare & Medicaid Services',
                'copyright': 'The POA Indicator Codes are in the public domain and are free to use without restriction.',  # pylint: disable=line-too-long
                'revisionDate': ANY,
                'experimental': False,
                'caseSensitive': True,
                'compositional': False,
                'versionNeeded': False,
                'concept': [{
                                'code': 'Y',
                                'display': 'Y',
                                'definition': 'Diagnosis was present at time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as "Y" for the POA Indicator.',  # pylint: disable=line-too-long
                                'designation': [{
                                                    'language': 'en',
                                                    'value': 'Y'
                                                }],
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'valueString': 'Misc'
                                             }, {
                                                 'code': 'datatype',
                                                 'valueString': 'N/A'
                                             }]
                            }, {
                                'code': 'N',
                                'display': 'N',
                                'definition': 'Diagnosis was not present at time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as "N" for the POA Indicator.',  # pylint: disable=line-too-long
                                'designation': [{
                                                    'language': 'en',
                                                    'value': 'N'
                                                }],
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'valueString': 'Misc'
                                             }, {
                                                 'code': 'datatype',
                                                 'valueString': 'N/A'
                                             }]
                            }, {
                                'code': 'U',
                                'display': 'U',
                                'definition': 'Documentation insufficient to determine if the condition was present at the time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as "U" for the POA Indicator.',  # pylint: disable=line-too-long
                                'designation': [{
                                                    'language': 'en',
                                                    'value': 'U'
                                                }],
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'valueString': 'Misc'
                                             }, {
                                                 'code': 'datatype',
                                                 'valueString': 'N/A'
                                             }]
                            }, {
                                'code': 'W',
                                'display': 'W',
                                'definition': 'Clinically undetermined.  Provider unable to clinically determine whether the condition was present at the time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as "W" for the POA Indicator.',  # pylint: disable=line-too-long
                                'designation': [{
                                                    'language': 'en',
                                                    'value': 'W'
                                                }],
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'valueString': 'Misc'
                                             }, {
                                                 'code': 'datatype',
                                                 'valueString': 'N/A'
                                             }]
                            }, {
                                'code': '1',
                                'display': '1',
                                'definition': 'Unreported/Not used.  Exempt from POA reporting.  This code is equivalent to a blank on the UB-04, however; it was determined that blanks are undesirable when submitting this data via the 4010A. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as "1" for the POA Indicator. The \\u201c1\\u201d POA Indicator should not be applied to any codes on the HAC list.  For a complete list of codes on the POA exempt list, see  the Official Coding Guidelines for ICD-10-CM.',  # pylint: disable=line-too-long
                                'designation': [{
                                                    'language': 'en',
                                                    'value': '1'
                                                }],
                                'property': [{
                                                 'code': 'conceptclass',
                                                 'valueString': 'Misc'
                                             }, {
                                                 'code': 'datatype',
                                                 'valueString': 'N/A'
                                             }]
                            }],
                'text': {
                    'status': 'generated',
                    'div': '<div xmlns="http://www.w3.org/1999/xhtml"><p>This case-sensitive code system <code>https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding</code> defines the following codes:</p><table class="codes"><tr><td style="white-space:nowrap"><b>Code</b></td><td><b>Definition</b></td></tr><tr><td style="white-space:nowrap">Y<a name="presentOnAdmission-Y"> </a></td><td>Diagnosis was present at time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as &quot;Y&quot; for the POA Indicator.</td></tr><tr><td style="white-space:nowrap">N<a name="presentOnAdmission-N"> </a></td><td>Diagnosis was not present at time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;N&quot; for the POA Indicator.</td></tr><tr><td style="white-space:nowrap">U<a name="presentOnAdmission-U"> </a></td><td>Documentation insufficient to determine if the condition was present at the time of inpatient admission. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;U&quot; for the POA Indicator.</td></tr><tr><td style="white-space:nowrap">W<a name="presentOnAdmission-W"> </a></td><td>Clinically undetermined.  Provider unable to clinically determine whether the condition was present at the time of inpatient admission. CMS will pay the CC/MCC DRG for those selected HACs that are coded as &quot;W&quot; for the POA Indicator.</td></tr><tr><td style="white-space:nowrap">1<a name="presentOnAdmission-1"> </a></td><td>Unreported/Not used.  Exempt from POA reporting.  This code is equivalent to a blank on the UB-04, however; it was determined that blanks are undesirable when submitting this data via the 4010A. CMS will not pay the CC/MCC DRG for those selected HACs that are coded as &quot;1&quot; for the POA Indicator. The \\u201c1\\u201d POA Indicator should not be applied to any codes on the HAC list.  For a complete list of codes on the POA exempt list, see  the Official Coding Guidelines for ICD-10-CM.</td></tr></table></div>'  # pylint: disable=line-too-long
                }
            })
        self.assertEqual(
            Source.objects.filter(version='07/14/2020').first().uri,
            '/orgs/OCL/sources/presentOnAdmission/07%252F14%252F2020/'
        )
        self.assertEqual(
            Source.objects.filter(version='HEAD').first().uri,
            '/orgs/OCL/sources/presentOnAdmission/'
        )
