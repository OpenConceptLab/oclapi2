from core.common.tests import OCLTestCase
from core.parameters.serializers import ParametersSerializer


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
