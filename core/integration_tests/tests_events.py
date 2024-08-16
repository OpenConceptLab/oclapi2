from core.common.tests import OCLAPITestCase
from core.events.tests.factories import EventFactory
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class EventsViewTest(OCLAPITestCase):
    def test_get(self):
        alfred = UserProfileFactory(username='alfred', first_name='Alfred', last_name='Pennyworth')
        bruce = UserProfileFactory(username='bruce', first_name='Bruce', last_name='Wayne')
        joker = UserProfileFactory(username='joker', first_name='Joker', last_name='The Clown')

        wayne_corp = OrganizationFactory(mnemonic='waynecorp', name='Wayne Enterprises')
        bat_cave = OrganizationFactory(mnemonic='batcave', name='Bat Cave')

        bruce.organizations.add(bat_cave)
        bruce.organizations.add(wayne_corp)
        alfred.organizations.add(bat_cave)

        bruce_joined_wayne_corp_event = EventFactory(
            object_url=bruce.url, referenced_object_url=wayne_corp.url, actor=bruce, event_type='Joined')
        bruce_created_bat_cave_event = EventFactory(
            object_url=bruce.url, referenced_object_url=bat_cave.url, actor=bruce, event_type='Created', public=False)

        alfred_subscribed_wayne_corp_event = EventFactory(
            object_url=alfred.url, referenced_object_url=wayne_corp.url, actor=alfred, event_type='Subscribed')
        alfred_followed_bruce_event = EventFactory(
            object_url=alfred.url, referenced_object_url=bruce.url, actor=alfred, event_type='Followed')
        alfred_joined_bat_cave_event = EventFactory(
            object_url=alfred.url, referenced_object_url=bat_cave.url, actor=bruce, event_type='Joined', public=False)

        response = self.client.get('/users/batman/events/')
        self.assertEqual(response.status_code, 404)

        response = self.client.get('/users/bruce/events/')

        self.assertEqual(response.status_code, 200)
        bruce_public_events = response.data
        self.assertEqual(len(bruce_public_events), 1)
        self.assertEqual(bruce_public_events[0]['event_type'], 'Joined')
        self.assertEqual(bruce_public_events[0]['type'], 'Event')
        self.assertEqual(bruce_public_events[0]['public'], True)
        self.assertEqual(bruce_public_events[0]['description'], bruce_joined_wayne_corp_event.description)
        self.assertEqual(bruce_public_events[0]['actor']['username'], 'bruce')
        self.assertEqual(bruce_public_events[0]['actor']['url'], '/users/bruce/')
        self.assertEqual(bruce_public_events[0]['object']['username'], 'bruce')
        self.assertEqual(bruce_public_events[0]['object']['url'], '/users/bruce/')
        self.assertEqual(bruce_public_events[0]['referenced_object']['id'], 'waynecorp')
        self.assertEqual(bruce_public_events[0]['referenced_object']['url'], '/orgs/waynecorp/')

        response = self.client.get(
            '/users/bruce/events/',
            HTTP_AUTHORIZATION=f'Token {joker.get_token()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, bruce_public_events)

        response = self.client.get(
            '/users/bruce/events/',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )

        self.assertEqual(response.status_code, 200)
        bruce_all_events = response.data
        self.assertEqual(len(bruce_all_events), 2)
        self.assertEqual(bruce_public_events[0], bruce_public_events[0])
        private_event = bruce_all_events[0]
        self.assertEqual(private_event['event_type'], 'Created')
        self.assertEqual(private_event['type'], 'Event')
        self.assertEqual(private_event['public'], False)
        self.assertEqual(private_event['description'], bruce_created_bat_cave_event.description)
        self.assertEqual(private_event['actor']['username'], 'bruce')
        self.assertEqual(private_event['actor']['url'], '/users/bruce/')
        self.assertEqual(private_event['object']['username'], 'bruce')
        self.assertEqual(private_event['object']['url'], '/users/bruce/')
        self.assertEqual(private_event['referenced_object']['id'], 'batcave')
        self.assertEqual(private_event['referenced_object']['url'], '/orgs/batcave/')

        response = self.client.get(
            '/users/alfred/events/',
            HTTP_AUTHORIZATION=f'Token {alfred.get_token()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertEqual(response.data[0]['id'], alfred_joined_bat_cave_event.id)
        self.assertEqual(response.data[1]['id'], alfred_followed_bruce_event.id)
        self.assertEqual(response.data[2]['id'], alfred_subscribed_wayne_corp_event.id)

        response = self.client.get(
            '/orgs/waynecorp/events/',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
