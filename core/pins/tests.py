import importlib

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from mock import Mock

from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.events.models import Event
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.pins.models import Pin
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile, Follow
from core.users.tests.factories import UserProfileFactory


class PinTest(OCLTestCase):
    def test_clean(self):
        org = OrganizationFactory()
        pin = Pin(resource=org)
        with self.assertRaises(ValidationError) as ex:
            pin.full_clean()

        self.assertEqual(
            ex.exception.message_dict, {'parent': ['Pin needs to be owned by a user or an organization.']}
        )

        pin.organization = org
        pin.full_clean()

    def test_resource_uri(self):
        self.assertEqual(Pin(resource=Organization(uri='/orgs/foo/')).resource_uri, '/orgs/foo/')
        self.assertEqual(Pin(resource=Source(uri='/orgs/foo/sources/bar/')).resource_uri, '/orgs/foo/sources/bar/')

    def test_get_resource(self):
        org = OrganizationFactory()
        source = OrganizationSourceFactory(organization=org)
        collection = OrganizationCollectionFactory(organization=org)

        self.assertEqual(Pin.get_resource(resource_type='Source', resource_id=source.id), source)
        self.assertEqual(Pin.get_resource(resource_type='collection', resource_id=collection.id), collection)
        self.assertEqual(Pin.get_resource(resource_type='org', resource_id=org.id), org)
        self.assertEqual(Pin.get_resource(resource_type='organization', resource_id=org.id), org)
        self.assertEqual(Pin.get_resource(resource_type='organization', resource_id=123), None)
        self.assertEqual(Pin.get_resource(resource_type='foobar', resource_id=123), None)

    def test_parent(self):
        org = OrganizationFactory()
        user = UserProfileFactory()

        self.assertEqual(Pin().parent, None)
        self.assertEqual(Pin(organization=org).parent, org)
        self.assertEqual(Pin(user=user).parent, user)
        self.assertEqual(Pin(user=user, organization=org).parent, org)

    def test_uri(self):
        org = OrganizationFactory(mnemonic='org-1')
        user = UserProfileFactory(username='user-1')

        self.assertEqual(Pin().uri, None)
        self.assertEqual(Pin(id=1, organization=org).uri, '/orgs/org-1/pins/1/')
        self.assertEqual(Pin(id=2, user=user).uri, '/users/user-1/pins/2/')

    def test_soft_delete(self):
        pin = Pin()
        pin.delete = Mock(return_value=True)

        self.assertEqual(pin.soft_delete(), True)
        pin.delete.assert_called_once()

    def test_default_order(self):
        org = OrganizationFactory(mnemonic='org-1')
        source = OrganizationSourceFactory(organization=org)
        user = UserProfileFactory(username='user-1')

        org_pin1 = Pin(organization=org, resource=source)
        org_pin1.save()
        self.assertEqual(org_pin1.order, 0)

        org_pin2 = Pin(organization=org, resource=OrganizationCollectionFactory(organization=org))
        org_pin2.save()
        self.assertEqual(org_pin2.order, 1)

        org_pin2.delete()

        org_pin3 = Pin(organization=org, resource=OrganizationCollectionFactory(organization=org))
        org_pin3.save()
        self.assertEqual(org_pin3.order, 1)

        user_pin1 = Pin(user=user, resource=source)
        user_pin1.save()
        self.assertEqual(user_pin1.order, 0)

        user_pin2 = Pin(user=user, resource=OrganizationCollectionFactory(organization=org))
        user_pin2.save()
        self.assertEqual(user_pin2.order, 1)

        user_pin2.delete()

        user_pin3 = Pin(user=user, resource=OrganizationCollectionFactory(organization=org))
        user_pin3.save()
        self.assertEqual(user_pin3.order, 1)

    def test_update_order(self):
        org = OrganizationFactory(mnemonic='org-1')

        org_pin1 = Pin(organization=org, resource=OrganizationSourceFactory(organization=org))
        org_pin1.save()
        self.assertEqual(org_pin1.order, 0)

        org_pin2 = Pin(organization=org, resource=OrganizationCollectionFactory(organization=org))
        org_pin2.save()
        self.assertEqual(org_pin2.order, 1)

        org_pin3 = Pin(organization=org, resource=OrganizationCollectionFactory(organization=org))
        org_pin3.save()
        self.assertEqual(org_pin3.order, 2)

        org_pin3.to(0)

        org_pin1.refresh_from_db()
        org_pin2.refresh_from_db()
        org_pin3.refresh_from_db()
        self.assertEqual(org_pin3.order, 0)
        self.assertEqual(org_pin1.order, 1)
        self.assertEqual(org_pin2.order, 2)

        org_pin3.to(2)
        org_pin1.refresh_from_db()
        org_pin2.refresh_from_db()
        org_pin3.refresh_from_db()
        self.assertEqual(org_pin1.order, 0)
        self.assertEqual(org_pin2.order, 1)
        self.assertEqual(org_pin3.order, 2)

        org_pin3.to(1)
        org_pin1.refresh_from_db()
        org_pin2.refresh_from_db()
        org_pin3.refresh_from_db()
        self.assertEqual(org_pin1.order, 0)
        self.assertEqual(org_pin3.order, 1)
        self.assertEqual(org_pin2.order, 2)


class PinListViewTest(OCLAPITestCase):
    def tearDown(self):
        Pin.objects.all().delete()
        super().tearDown()

    def test_get_200(self):
        source = OrganizationSourceFactory()
        user = UserProfileFactory()
        org = OrganizationFactory()
        token = user.get_token()

        response = self.client.get(
            user.uri + 'pins/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        pin1 = user.pins.create(resource=source)
        pin2 = org.pins.create(resource=source)

        response = self.client.get(
            user.uri + 'pins/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resource_uri'], source.uri)
        self.assertEqual(response.data[0]['id'], pin1.id)
        self.assertIsNotNone(response.data[0]['uri'])
        self.assertIsNone(response.data[0]['organization_id'])

        response = self.client.get(
            org.uri + 'pins/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resource_uri'], source.uri)
        self.assertEqual(response.data[0]['id'], pin2.id)
        self.assertEqual(response.data[0]['organization_id'], org.id)
        self.assertIsNotNone(response.data[0]['uri'])
        self.assertIsNone(response.data[0]['user_id'])

    def test_get_200_with_creator_pins(self):
        source = OrganizationSourceFactory()
        collection = OrganizationCollectionFactory()
        user = UserProfileFactory()
        org = OrganizationFactory()
        user.pins.create(resource=source)
        org.pins.create(resource=source)
        org.pins.create(resource=collection, created_by=user)
        token = user.get_token()

        response = self.client.get(
            user.uri + 'pins/',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['resource_uri'], source.uri)

        response = self.client.get(
            user.uri + 'pins/?includeCreatorPins=true',
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(
            sorted([resource['resource_uri'] for resource in response.data]),
            sorted([source.uri, collection.uri])
        )

    def test_get_200_only_pins_the_requester_can_view(self):
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)
        public_org = OrganizationFactory()
        owner = UserProfileFactory(organizations=[private_org, public_org])
        private_source = OrganizationSourceFactory(organization=private_org, public_access=ACCESS_TYPE_NONE)
        public_source = OrganizationSourceFactory(organization=public_org)
        private_user_pin = owner.pins.create(resource=private_source)
        owner.pins.create(resource=public_source)
        owner.pins.create(resource=private_org)
        public_org.pins.create(resource=private_source)
        public_org.pins.create(resource=public_source)
        private_org.pins.create(resource=public_source)
        admin = UserProfile.objects.get(username='ocladmin')

        def get_pin_uris(url, token=None):
            headers = {'HTTP_AUTHORIZATION': 'Token ' + token} if token else {}
            response = self.client.get(url, format='json', **headers)
            self.assertEqual(response.status_code, 200)
            return sorted(pin['resource_uri'] for pin in response.data)

        for token in [None, UserProfileFactory().get_token()]:
            self.assertEqual(get_pin_uris(owner.uri + 'pins/', token), [public_source.uri])
            self.assertEqual(get_pin_uris(public_org.uri + 'pins/', token), [public_source.uri])
            self.assertEqual(get_pin_uris(private_org.uri + 'pins/', token), [])
            headers = {'HTTP_AUTHORIZATION': 'Token ' + token} if token else {}
            response = self.client.get(
                owner.uri + f'pins/{private_user_pin.id}/', format='json', **headers)
            self.assertEqual(response.status_code, 404)

        for token in [owner.get_token(), admin.get_token()]:
            self.assertEqual(
                get_pin_uris(owner.uri + 'pins/', token),
                sorted([private_source.uri, public_source.uri, private_org.uri]))
            self.assertEqual(
                get_pin_uris(public_org.uri + 'pins/', token), sorted([private_source.uri, public_source.uri]))
            self.assertEqual(get_pin_uris(private_org.uri + 'pins/', token), [public_source.uri])

        private_org.members.remove(owner)
        self.assertEqual(get_pin_uris(owner.uri + 'pins/', owner.get_token()), [public_source.uri])
        response = self.client.get(
            owner.uri + '?includePins=true', HTTP_AUTHORIZATION='Token ' + owner.get_token(), format='json')
        self.assertEqual([pin['resource_uri'] for pin in response.data['pins']], [public_source.uri])

        # an update would return the resource, so it's refused; the owner can still delete the pin
        pin_url = owner.uri + f'pins/{private_user_pin.id}/'
        response = self.client.patch(
            pin_url, {'order': 0}, HTTP_AUTHORIZATION='Token ' + owner.get_token(), format='json')
        self.assertEqual(response.status_code, 404)
        response = self.client.delete(pin_url, HTTP_AUTHORIZATION='Token ' + owner.get_token(), format='json')
        self.assertEqual(response.status_code, 204)

    def test_post_201(self):
        source = OrganizationSourceFactory()
        org = OrganizationFactory()
        user = UserProfileFactory(organizations=[org])
        token = user.get_token()

        response = self.client.post(
            user.uri + 'pins/',
            {
                'resource_type': 'Source',
                'resource_id': source.id
            },
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['resource_uri'], source.uri)
        self.assertEqual(response.data['user_id'], user.id)
        self.assertIsNone(response.data['organization_id'])
        self.assertIsNotNone(response.data['resource'])
        self.assertIsNotNone(response.data['uri'])

        response = self.client.post(
            org.uri + 'pins/',
            {
                'resource_type': 'Source',
                'resource_id': source.id
            },
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['resource_uri'], source.uri)
        self.assertEqual(response.data['organization_id'], org.id)
        self.assertIsNone(response.data['user_id'])
        self.assertIsNotNone(response.data['resource'])
        self.assertIsNotNone(response.data['uri'])

    def test_post_400(self):
        user = UserProfileFactory()
        token = user.get_token()

        response = self.client.post(
            user.uri + 'pins/',
            {
                'resource_type': 'Source',
                'resource_id': 1209
            },
            HTTP_AUTHORIZATION='Token ' + token,
            format='json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'resource': 'Resource type Source with id 1209 does not exists.'})


class PinRetrieveUpdateDestroyViewTest(OCLAPITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.user = UserProfileFactory(organizations=[self.org])
        self.token = self.user.get_token()
        self.source = OrganizationSourceFactory()
        self.user_pin = self.user.pins.create(resource=self.source)
        self.org_pin = self.org.pins.create(resource=self.source)

    def test_get_200(self):
        response = self.client.get(
            self.user.uri + 'pins/' + str(self.user_pin.id) + '/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resource_uri'], self.source.uri)
        self.assertEqual(response.data['resource']['id'], self.source.mnemonic)
        self.assertEqual(response.data['user_id'], self.user.id)
        self.assertEqual(response.data['order'], 0)

        response = self.client.get(
            self.org.uri + 'pins/' + str(self.org_pin.id) + '/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resource_uri'], self.source.uri)
        self.assertEqual(response.data['resource']['id'], self.source.mnemonic)
        self.assertEqual(response.data['organization_id'], self.org.id)
        self.assertEqual(response.data['order'], 0)

    def test_delete_204(self):
        response = self.client.delete(
            self.org.uri + 'pins/' + str(self.org_pin.id) + '/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.org.pins.count(), 0)

        response = self.client.delete(
            self.user.uri + 'pins/' + str(self.user_pin.id) + '/',
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.user.pins.count(), 0)

    def test_put_200(self):
        user_pin2 = self.user.pins.create(resource=self.org)
        self.assertEqual(self.user_pin.order, 0)
        self.assertEqual(user_pin2.order, 1)

        response = self.client.put(
            self.user.uri + 'pins/' + str(user_pin2.id) + '/',
            {'order': 0},
            HTTP_AUTHORIZATION='Token ' + self.token,
            format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['order'], 0)
        self.assertEqual(response.data['id'], user_pin2.id)

        self.user_pin.refresh_from_db()
        user_pin2.refresh_from_db()
        self.assertEqual(self.user_pin.order, 1)
        self.assertEqual(user_pin2.order, 0)


class PinsToFollowsMigrationTest(OCLAPITestCase):
    def test_migration_turns_my_pins_into_follows(self):  # pylint: disable=too-many-locals
        migration = importlib.import_module('core.pins.migrations.0005_pins_to_follows')
        private_org = OrganizationFactory(public_access=ACCESS_TYPE_NONE)
        public_org = OrganizationFactory()
        member = UserProfileFactory(organizations=[private_org, public_org])
        former_member = UserProfileFactory()  # left private_org
        outsider = UserProfileFactory()
        admin = UserProfile.objects.get(username='ocladmin')
        public_source = OrganizationSourceFactory(organization=public_org)
        public_collection = OrganizationCollectionFactory(organization=public_org)
        public_version = OrganizationSourceFactory(
            organization=public_org, mnemonic=public_source.mnemonic, version='v1')
        private_source = OrganizationSourceFactory(organization=private_org, public_access=ACCESS_TYPE_NONE)
        public_version_of_private_source = OrganizationSourceFactory(
            organization=private_org, mnemonic=private_source.mnemonic, version='v1')
        private_collection = UserCollectionFactory(user=member, public_access=ACCESS_TYPE_NONE)
        deleted_source = OrganizationSourceFactory(organization=public_org)
        inactive_collection = OrganizationCollectionFactory(organization=public_org, is_active=False)

        for resource in [
                public_source, private_source, private_collection, private_org, deleted_source, inactive_collection]:
            member.pins.create(resource=resource)
        for resource in [public_source, private_source, private_collection, private_org]:
            former_member.pins.create(resource=resource)
        for resource in [public_version, public_version_of_private_source]:
            outsider.pins.create(resource=resource)
        # org pins go to their creator, while the creator can view the org too
        public_org.pins.create(resource=public_source, created_by=member)
        public_org.pins.create(resource=public_collection, created_by=former_member)
        public_org.pins.create(resource=private_source, created_by=former_member)
        private_org.pins.create(resource=public_source, created_by=outsider)
        private_org.pins.create(resource=private_collection)  # created by ocladmin, a staff user
        Source.objects.filter(id=deleted_source.id).delete()
        existing_follow = Follow.objects.create(follower=member, following=public_source)
        pins = list(Pin.objects.order_by('id').values())
        event_count = Event.objects.count()

        historical_apps = MigrationLoader(connection).project_state(('pins', '0005_pins_to_follows')).apps
        migration.create_follows_from_pins(historical_apps, None)
        migration.create_follows_from_pins(historical_apps, None)  # running it again adds nothing

        def get_followed_uris(user):
            return sorted(follow.following.uri for follow in user.following.all())

        self.assertEqual(
            get_followed_uris(member),
            sorted([public_source.uri, private_source.uri, private_collection.uri, private_org.uri]))
        self.assertEqual(get_followed_uris(former_member), sorted([public_source.uri, public_collection.uri]))
        self.assertEqual(get_followed_uris(outsider), [public_version.uri])
        self.assertEqual(get_followed_uris(admin), [private_collection.uri])
        self.assertEqual(Follow.objects.count(), 8)
        self.assertEqual(Follow.objects.get(follower=member, following_id=public_source.id).id, existing_follow.id)
        self.assertEqual(list(Pin.objects.order_by('id').values()), pins)
        self.assertEqual(Event.objects.count(), event_count)

        # the follows are what each person's TBv2 My Pins list showed, less the deleted resources
        for user in [member, former_member, outsider, admin]:
            response = self.client.get(
                user.uri + 'pins/?includeCreatorPins=true', HTTP_AUTHORIZATION='Token ' + user.get_token())
            self.assertEqual(
                sorted({pin['resource_uri'] for pin in response.data} - {None, inactive_collection.uri}),
                get_followed_uris(user))
