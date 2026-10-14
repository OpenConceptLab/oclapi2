import importlib

from django.apps import apps
from django.utils import timezone

from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.events.models import Event
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


class EventTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.actor = UserProfileFactory(username='butler', first_name='Alfred', last_name='Pennyworth')
        self.now = timezone.now()
        self.user_event = Event(
            object_url='/users/batman/', id=1, actor=self.actor, event_type='Joined', created_at=self.now)
        self.org_event = Event(
            object_url='/orgs/waynecorp/', id=2, actor=self.actor, event_type='Subscribed', created_at=self.now)

    def test_type(self):
        self.assertEqual(Event().type, 'Event')
        self.assertEqual(self.user_event.type, 'Event')
        self.assertEqual(self.org_event.type, 'Event')

    def test_url(self):
        self.assertEqual(self.user_event.url, '/users/batman/events/1/')
        self.assertEqual(self.org_event.url, '/orgs/waynecorp/events/2/')

    def test_object(self):
        self.assertEqual(Event().object, None)
        self.assertEqual(self.user_event.object, None)
        self.assertEqual(self.org_event.object, None)

        batman = UserProfileFactory(username='batman')
        wayne_corp = OrganizationFactory(mnemonic='waynecorp')

        self.assertEqual(self.user_event.object, batman)
        self.assertEqual(self.org_event.object, wayne_corp)

    def test_object_repr(self):
        self.assertEqual(Event().object_repr, None)
        self.assertEqual(self.user_event.object_repr, None)
        self.assertEqual(self.org_event.object_repr, None)

        UserProfileFactory(username='batman')
        OrganizationFactory(mnemonic='waynecorp')

        self.assertEqual(self.user_event.object_repr, "User:batman")
        self.assertEqual(self.org_event.object_repr, "Organization:waynecorp")

    def test_referenced_object(self):
        self.assertEqual(Event().referenced_object, None)
        self.assertEqual(self.user_event.referenced_object, None)
        self.assertEqual(self.org_event.referenced_object, None)

        batman = UserProfileFactory(username='batman')
        wayne_corp = OrganizationFactory(mnemonic='waynecorp')

        self.user_event.referenced_object_url = '/orgs/waynecorp/'
        self.assertEqual(self.user_event.referenced_object, wayne_corp)

        self.org_event.referenced_object_url = batman.uri
        self.assertEqual(self.org_event.referenced_object, batman)

        concept = ConceptFactory()
        self.user_event.referenced_object_url = concept.uri
        self.assertEqual(self.user_event.referenced_object, concept)

        mapping = MappingFactory()
        self.user_event.referenced_object_url = mapping.uri
        self.assertEqual(self.user_event.referenced_object, mapping)

        source = OrganizationSourceFactory()
        self.user_event.referenced_object_url = source.uri
        self.assertEqual(self.user_event.referenced_object, source)

        self.user_event.referenced_object_url = '/orgs/foo/collections/collection/'
        self.assertEqual(self.user_event.referenced_object, None)

    def test_referenced_object_repr(self):
        self.assertEqual(Event().referenced_object_repr, None)
        self.assertEqual(self.user_event.referenced_object_repr, None)
        self.assertEqual(self.org_event.referenced_object_repr, None)

        batman = UserProfileFactory(username='batman')
        OrganizationFactory(mnemonic='waynecorp')

        self.user_event.referenced_object_url = '/orgs/waynecorp/'
        self.assertEqual(self.user_event.referenced_object_repr, "Organization:waynecorp")

        self.org_event.referenced_object_url = batman.uri
        self.assertEqual(self.org_event.referenced_object_repr, "User:batman")

    def test_description(self):
        batman = UserProfileFactory(username='batman', first_name='Bruce', last_name='Wayne')
        OrganizationFactory(mnemonic='waynecorp', name='Wayne Enterprises')

        self.user_event.referenced_object_url = '/orgs/waynecorp/'
        self.assertEqual(self.user_event.description, "Joined Organization:waynecorp")

        self.org_event.referenced_object_url = batman.uri
        self.assertEqual(self.org_event.description, "Subscribed User:batman")

    def test_clean_fields(self):
        self.user_event.referenced_object_url = '/orgs/waynecorp/'
        self.user_event.public = None

        self.user_event.clean_fields()
        self.assertEqual(self.user_event.public, False)


class PrivateFollowEventsMigrationTest(OCLTestCase):
    def test_migration_makes_follow_events_of_private_objects_private(self):  # pylint: disable=too-many-locals
        migration = importlib.import_module('core.events.migrations.0011_private_follow_events')
        follower = UserProfileFactory()
        private_org = OrganizationFactory(public_access='None')
        public_org = OrganizationFactory()
        private_source = OrganizationSourceFactory(organization=public_org, public_access='None')
        public_source = OrganizationSourceFactory(organization=private_org)
        private_concept = ConceptFactory(parent=private_source)
        public_concept = ConceptFactory(parent=public_source)
        private_version = OrganizationSourceFactory(
            organization=public_source.organization, mnemonic=public_source.mnemonic, version='v1',
            public_access='None')
        private_version.refresh_from_db()
        urls = [
            follower.url, private_org.url, public_org.url, private_source.url, public_source.url,
            private_concept.url, public_concept.url, '/orgs/deleted-org/', private_version.uri
        ]
        events = {
            (event_type, url): Event.objects.create(
                object_url=follower.url, referenced_object_url=url, event_type=event_type, actor=follower)
            for url in urls for event_type in ['Followed', 'Unfollowed', 'Created']
        }

        migration.make_follow_events_of_private_objects_private(apps, None)

        private_urls = [private_org.url, private_source.url, private_concept.url, private_version.uri]
        for (event_type, url), event in events.items():
            event.refresh_from_db()
            self.assertEqual(event.public, event_type == 'Created' or url not in private_urls, (event_type, url))
