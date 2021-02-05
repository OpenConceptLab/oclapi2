from django.core.exceptions import ValidationError

from core.client_configs.models import ClientConfig
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory


class ClientConfigTest(OCLTestCase):
    def tearDown(self):
        ClientConfig.objects.all().delete()
        super().tearDown()

    def test_is_home(self):
        self.assertTrue(ClientConfig().is_home)
        self.assertTrue(ClientConfig(type='home').is_home)
        self.assertFalse(ClientConfig(type='blah').is_home)

    def test_home_config_validation(self):
        client_config = ClientConfig(config=dict())

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(
            ex.exception.message_dict,
            dict(
                config=['This field cannot be blank.'],
                resource_type=['This field cannot be null.'],
                resource_id=['This field cannot be null.'],
                tabs=['At least one tab config is mandatory.'],
            )
        )

        org = OrganizationFactory()
        client_config.resource = org
        client_config.config = dict(foo='bar')

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(ex.exception.message_dict, dict(tabs=['At least one tab config is mandatory.']))

        client_config.config = dict(tabs='foobar')

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(ex.exception.message_dict, dict(tabs=['Tabs config must be a list.']))

        client_config.config = dict(tabs=['foobar'])

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(ex.exception.message_dict, dict(tabs=['Invalid Tabs config.']))

        client_config.config = dict(tabs=[dict(foo='bar')])

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(ex.exception.message_dict, dict(tabs=['Exactly one of the Tabs must be default.']))

        client_config.config = dict(tabs=[dict(foo='bar', default=True), dict(foo='bar', default=True)])

        with self.assertRaises(ValidationError) as ex:
            client_config.full_clean()

        self.assertEqual(ex.exception.message_dict, dict(tabs=['Exactly one of the Tabs must be default.']))

        client_config.config = dict(tabs=[dict(foo='bar', default=True), dict(foo='bar', default=False)])
        client_config.full_clean()

    def test_uri(self):
        self.assertEqual(ClientConfig(id=1).uri, '/client-configs/1/')
        self.assertEqual(ClientConfig(id=400).uri, '/client-configs/400/')

    def test_siblings(self):
        org = OrganizationFactory()
        config1 = ClientConfig(name='first', resource=org, config=dict(tabs=[dict(foo='bar', default=True)]))
        config1.save()

        self.assertEqual(config1.siblings.count(), 0)

        config2 = ClientConfig(name='second', resource=org, config=dict(tabs=[dict(foo='bar', default=True)]))
        config2.save()

        self.assertEqual(config1.siblings.count(), 1)
        self.assertEqual(config1.siblings.first().id, config2.id)

        self.assertEqual(config2.siblings.count(), 1)
        self.assertEqual(config2.siblings.first().id, config1.id)


class ClientConfigsViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.user = self.org.created_by
        self.token = self.user.get_token()
        self.dummy_config = dict(tabs=[dict(default=True)])

    def tearDown(self):
        ClientConfig.objects.all().delete()
        super().tearDown()

    def test_post(self):
        response = self.client.post(
            self.org.url + 'client-configs/',
            dict(),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {'config': ['This field cannot be null.'], 'tabs': ['At least one tab config is mandatory.']}
        )

        response = self.client.post(
            self.org.url + 'client-configs/',
            dict(name='custom', config=self.dummy_config, is_default=True),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        config1 = ClientConfig.objects.last()
        self.assertEqual(config1.resource, self.org)
        self.assertEqual(config1.name, 'custom')
        self.assertEqual(config1.type, 'home')
        self.assertEqual(config1.config, self.dummy_config)
        self.assertEqual(config1.created_by, self.user)
        self.assertEqual(config1.updated_by, self.user)
        self.assertTrue(config1.is_default)

        response = self.client.post(
            self.org.url + 'client-configs/',
            dict(name='custom1', config=self.dummy_config, is_default=True),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 201)
        config2 = ClientConfig.objects.last()
        config1.refresh_from_db()
        self.assertTrue(config2.is_default)
        self.assertFalse(config1.is_default)

    def test_get(self):
        response = self.client.get(
            self.org.url + 'client-configs/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        config = ClientConfig(config=self.dummy_config, name='foobar', resource=self.org)
        config.save()

        response = self.client.get(
            self.org.url + 'client-configs/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], config.id)


class ClientConfigViewTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory()
        self.user = self.org.created_by
        self.token = self.user.get_token()
        self.dummy_config = dict(tabs=[dict(default=True)])
        self.config = ClientConfig(config=self.dummy_config, name='foobar', resource=self.org)
        self.config.save()

    def tearDown(self):
        ClientConfig.objects.all().delete()
        super().tearDown()

    def test_put(self):
        response = self.client.get(
            '/client-configs/12356/',
            dict(name='updated'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.put(
            self.config.uri,
            dict(name='updated'),
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.config.refresh_from_db()
        self.assertTrue(response.data['name'] == self.config.name == 'updated')
