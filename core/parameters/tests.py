from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptNameFactory, ConceptFactory
from core.parameters.serializers import ParametersSerializer
from core.sources.tests.factories import OrganizationSourceFactory


class ParametersTest(OCLTestCase):
    class CustomParametersSerializer(ParametersSerializer):
        allowed_input_parameters = {
            'url': 'valueUri',
            'code': 'valueCode',
            'system': 'valueUri'
        }

    def test_parse_query_params(self):
        query_params = {'url': 'https://openconceptlab.org', 'code': 'testCode', 'otherParam': 'otherValue'}
        serializer = self.CustomParametersSerializer.parse_query_params(query_params)
        self.assertTrue(serializer.is_valid())
        self.assertIsInstance(serializer, self.CustomParametersSerializer)
        self.assertEqual(
            serializer.validated_data, {'parameters': {'url': 'https://openconceptlab.org', 'code': 'testCode'}})

    def test_from_concept(self):
        source = OrganizationSourceFactory(default_locale='fr', supported_locales=['fr', 'ti'])
        ch_locale = ConceptNameFactory.build(locale_preferred=True, locale='ch')
        en_locale = ConceptNameFactory.build(locale_preferred=True, locale='en')
        concept = ConceptFactory(names=[ch_locale, en_locale], parent=source)
        serializer = ParametersSerializer.from_concept(concept)
        params = serializer.data['parameter']

        self.assertIsInstance(serializer, ParametersSerializer)
        self.assertEqual(len(params), 3)
        self.assertEqual(params[0]['name'], 'name')
        self.assertEqual(params[0]['valueString'], source.name)
        self.assertEqual(params[1]['name'], 'version')
        self.assertEqual(params[1]['valueString'], source.version)
        self.assertEqual(params[2]['name'], 'display')
        self.assertEqual(params[2]['valueString'], concept.display_name)
