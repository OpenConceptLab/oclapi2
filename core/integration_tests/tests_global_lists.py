import time

import psycopg2
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


class GlobalListIncludeRetiredTest(OCLAPITestCase):
    """The global concept and mapping lists with includeRetired and no q: a DB list counted with the retired rows."""
    def setUp(self):
        super().setUp()
        self.source = OrganizationSourceFactory()
        self.active_concept = ConceptFactory(parent=self.source)
        self.retired_concept = ConceptFactory(parent=self.source, retired=True)
        self.active_mapping = MappingFactory(parent=self.source)
        self.retired_mapping = MappingFactory(parent=self.source, retired=True)
        self.private_concept = ConceptFactory(parent=self.source, public_access=ACCESS_TYPE_NONE)
        self.private_mapping = MappingFactory(parent=self.source, public_access=ACCESS_TYPE_NONE)
        member = UserProfileFactory()
        self.source.organization.members.add(member)
        # token (None: anonymous) -> whether that requester sees the private rows
        self.requesters = {
            None: False, UserProfileFactory().get_token(): False, member.get_token(): True,
            UserProfileFactory(is_staff=True).get_token(): True,
        }

    def assert_list(self, url, active, retired, private):
        for token, sees_private in self.requesters.items():  # anonymous, non-staff, org member, staff
            auth = {'HTTP_AUTHORIZATION': 'Token ' + token} if token else {}

            response = self.client.get(url + '?limit=100', format='json', **auth)
            self.assertEqual(response.status_code, 200)
            uris = [row['url'] for row in response.data]
            self.assertIn(active.uri, uris)
            self.assertNotIn(retired.uri, uris)
            self.assertEqual(private.uri in uris, sees_private)
            without_retired = int(response['num_found'])

            response = self.client.get(url + '?limit=100&includeRetired=true', format='json', **auth)
            self.assertEqual(response.status_code, 200)
            uris = [row['url'] for row in response.data]
            self.assertIn(active.uri, uris)
            self.assertIn(retired.uri, uris)
            self.assertEqual(private.uri in uris, sees_private)
            self.assertEqual(int(response['num_found']), without_retired + 1)
            self.assertEqual(int(response['num_found']), len(uris))

    def test_concepts(self):
        self.assert_list('/concepts/', self.active_concept, self.retired_concept, self.private_concept)

    def test_mappings(self):
        self.assert_list('/mappings/', self.active_mapping, self.retired_mapping, self.private_mapping)


class ListQueryTimeoutTest(TransactionTestCase):
    @override_settings(LIST_QUERY_TIMEOUT_MS=200)
    def test_a_list_that_cannot_finish_is_cut_off(self):
        locker = psycopg2.connect(**connection.get_connection_params())
        try:
            locker.cursor().execute('LOCK TABLE concepts IN ACCESS EXCLUSIVE MODE')
            started = time.monotonic()
            with self.assertLogs('oclapi', level='WARNING'):
                response = APIClient().get('/concepts/?includeRetired=true&limit=5')
            elapsed = time.monotonic() - started
        finally:
            locker.rollback()
            locker.close()

        self.assertLess(elapsed, 5)
        self.assertEqual(response.status_code, 503)
        self.assertIn('took too long', response.data['detail'])

        # the timeout was local to the list's transaction
        with connection.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            self.assertEqual(cursor.fetchone()[0], '0')
        self.assertEqual(APIClient().get('/concepts/?includeRetired=true&limit=5').status_code, 200)
