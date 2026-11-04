import warnings

from django.test import SimpleTestCase
from fhir.resources.R4B.codesystem import CodeSystem

from core.code_systems.converter import CodeSystemConverter
from core.sources.tests.factories import OrganizationSourceFactory


class CodeSystemConverterR4Test(SimpleTestCase):
    """The converter speaks R4 through fhir.resources 8's R4B models, without its deprecated API."""

    def test_to_fhir_builds_an_r4_code_system(self):
        source = OrganizationSourceFactory.build(
            mnemonic='test', canonical_url='http://example.org/cs', name='Test', default_locale='en', released=True)

        with warnings.catch_warnings():
            warnings.simplefilter('error')
            code_system = CodeSystemConverter.to_fhir(source)

        self.assertEqual(code_system['resourceType'], 'CodeSystem')
        self.assertEqual(code_system['url'], 'http://example.org/cs')
        self.assertEqual(code_system['status'], 'active')
        CodeSystem.model_validate(code_system)

    def test_from_fhir_reads_an_r4_code_system(self):
        code_system = CodeSystem(id='test', status='retired', content='complete', url='http://example.org/cs')

        self.assertTrue(CodeSystemConverter.can_convert_from_fhir(code_system))
        source = CodeSystemConverter.from_fhir(code_system.model_dump())

        self.assertEqual(source['mnemonic'], 'test')
        self.assertEqual(source['canonical_url'], 'http://example.org/cs')
        self.assertTrue(source['retired'])
