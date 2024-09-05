from core.common.tests import OCLAPITestCase
from core.events.tests.factories import EventFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.tests.factories import UserProfileFactory


class EventsViewTest(OCLAPITestCase):
    def test_get(self):  # pylint:disable=too-many-statements,too-many-locals
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
        wayne_corp_events = response.data
        self.assertEqual(len(wayne_corp_events), 3)
        self.assertEqual(wayne_corp_events[2]['event_type'], 'Created')
        self.assertEqual(wayne_corp_events[2]['object']['url'], wayne_corp.created_by.uri)
        self.assertEqual(wayne_corp_events[2]['referenced_object']['url'], wayne_corp.uri)
        self.assertEqual(wayne_corp_events[1]['id'], bruce_joined_wayne_corp_event.id)
        self.assertEqual(wayne_corp_events[0]['id'], alfred_subscribed_wayne_corp_event.id)

        response = self.client.get(
            '/orgs/waynecorp/events/?scopes=self',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, wayne_corp_events)


class UserEventsViewTest(OCLAPITestCase):
    def test_get_following_events(self):  # pylint:disable=too-many-statements,too-many-locals
        alfred = UserProfileFactory(username='alfred', first_name='Alfred', last_name='Pennyworth')
        bruce = UserProfileFactory(username='bruce', first_name='Bruce', last_name='Wayne')
        joker = UserProfileFactory(username='joker', first_name='Joker', last_name='The Clown')
        penguin = UserProfileFactory(username='penguin', first_name='Oswald', last_name='Cobblepot')
        mario = UserProfileFactory(username='mario', first_name='Mario', last_name='Ito')

        wayne_corp = OrganizationFactory(mnemonic='waynecorp', name='Wayne Enterprises')
        bat_cave = OrganizationFactory(mnemonic='batcave', name='Bat Cave')
        gotham = OrganizationFactory(mnemonic='gotham', name='Gotham City')
        gotham_gazette = OrganizationFactory(mnemonic='gotham_gazette', name='Gotham Gazette')
        gotham_gazette_crime_repo = OrganizationSourceFactory(
            mnemonic='gotham_gazette_crime', name='Gotham Gazette Crime Unit', organization=gotham_gazette,
            public_access='None')

        bruce.organizations.add(bat_cave)
        bruce.organizations.add(wayne_corp)
        bruce.organizations.add(gotham)
        alfred.organizations.add(bat_cave)
        alfred.organizations.add(gotham)
        joker.organizations.add(gotham)
        penguin.organizations.add(gotham)
        mario.organizations.add(gotham_gazette)

        alfred.follow(bruce)  # Creates Event
        joker.follow(bruce)  # Creates Event
        bruce.follow(joker)  # Creates Event
        bruce.follow(penguin)  # Creates Event
        bruce.follow(gotham_gazette)  # Creates Event
        bruce.follow(gotham_gazette_crime_repo)  # Creates Event

        EventFactory(
            object_url=bruce.url, referenced_object_url=bat_cave.url, actor=bruce, event_type='Joined', public=False)
        EventFactory(
            object_url=bruce.url, referenced_object_url=wayne_corp.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=bruce.url, referenced_object_url=gotham.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=gotham.url, actor=alfred, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=bat_cave.url, actor=alfred, event_type='Joined', public=False)
        EventFactory(
            object_url=joker.url, referenced_object_url=gotham.url, actor=joker, event_type='Joined')
        EventFactory(
            object_url=penguin.url, referenced_object_url=gotham.url, actor=penguin, event_type='Joined')
        EventFactory(
            object_url=mario.url, referenced_object_url=gotham_gazette.url, actor=mario, event_type='Joined')

        response = self.client.get('/users/batman/events/?scopes=following')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            '/users/bruce/events/?scopes=following'
        )
        self.assertEqual(response.status_code, 200)
        # Bruce is following Joker, Penguin and Gotham Gazette
        # 1. Mario's event of joining Gotham Gazette
        # 2. Penguin's event of joining Gotham
        # 3. Joker's event of joining Gotham
        # 4. Bruce's follow of Gotham Gazette Crime Deptt
        # 5. Bruce's follow of Gotham Gazette
        # 6. Joker's event of following Bruce
        # 7. OCL's event of creation of Gotham Gazette
        self.assertEqual(len(response.data), 7)
        self.assertEqual(response.data[0]['object']['url'], mario.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[1]['object']['url'], penguin.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], joker.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[3]['object']['url'], bruce.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[4]['object']['url'], bruce.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[5]['object']['url'], joker.url)
        self.assertEqual(response.data[5]['referenced_object']['url'], bruce.url)
        self.assertEqual(response.data[6]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[6]['referenced_object']['url'], gotham_gazette.url)

        response = self.client.get(
            '/users/bruce/events/?scopes=following',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )
        self.assertEqual(response.status_code, 200)
        # Bruce is following Joker, Penguin and Gotham Gazette
        # 1. Mario's event of joining Gotham Gazette
        # 2. Penguin's event of joining Gotham
        # 3. Joker's event of joining Gotham
        # 4. Bruce's follow of Gotham Gazette Crime Deptt
        # 5. Bruce's follow of Gotham Gazette
        # 6. Joker's event of following Bruce
        # 7. OCL's event of creation of Gotham Gazette Crime Deptt
        # 8. OCL's event of creation of Gotham Gazette
        self.assertEqual(len(response.data), 8)
        self.assertEqual(response.data[0]['object']['url'], mario.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[1]['object']['url'], penguin.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], joker.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[3]['object']['url'], bruce.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[4]['object']['url'], bruce.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[5]['object']['url'], joker.url)
        self.assertEqual(response.data[5]['referenced_object']['url'], bruce.url)
        self.assertEqual(response.data[6]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[6]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[7]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[7]['referenced_object']['url'], gotham_gazette.url)

    def test_get_orgs_events(self):  # pylint:disable=too-many-statements,too-many-locals
        alfred = UserProfileFactory(username='alfred', first_name='Alfred', last_name='Pennyworth')
        bruce = UserProfileFactory(username='bruce', first_name='Bruce', last_name='Wayne')
        joker = UserProfileFactory(username='joker', first_name='Joker', last_name='The Clown')
        penguin = UserProfileFactory(username='penguin', first_name='Oswald', last_name='Cobblepot')
        mario = UserProfileFactory(username='mario', first_name='Mario', last_name='Ito')

        wayne_corp = OrganizationFactory(mnemonic='waynecorp', name='Wayne Enterprises')
        bat_cave = OrganizationFactory(mnemonic='batcave', name='Bat Cave', public_access='None')
        gotham = OrganizationFactory(mnemonic='gotham', name='Gotham City')
        gotham_gazette = OrganizationFactory(mnemonic='gotham_gazette', name='Gotham Gazette')
        gotham_gazette_crime_repo = OrganizationSourceFactory(
            mnemonic='gotham_gazette_crime', name='Gotham Gazette Crime Unit', organization=gotham_gazette,
            public_access='None')

        bruce.organizations.add(bat_cave)
        bruce.organizations.add(wayne_corp)
        bruce.organizations.add(gotham)
        alfred.organizations.add(bat_cave)
        alfred.organizations.add(gotham)
        joker.organizations.add(gotham)
        penguin.organizations.add(gotham)
        mario.organizations.add(gotham_gazette)

        alfred.follow(bruce)  # Creates Event
        joker.follow(bruce)  # Creates Event
        bruce.follow(joker)  # Creates Event
        bruce.follow(penguin)  # Creates Event
        bruce.follow(gotham_gazette)  # Creates Event
        bruce.follow(gotham_gazette_crime_repo)  # Creates Event

        EventFactory(
            object_url=bruce.url, referenced_object_url=bat_cave.url, actor=bruce, event_type='Joined', public=False)
        EventFactory(
            object_url=bruce.url, referenced_object_url=wayne_corp.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=bruce.url, referenced_object_url=gotham.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=gotham.url, actor=alfred, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=bat_cave.url, actor=alfred, event_type='Joined', public=False)
        EventFactory(
            object_url=joker.url, referenced_object_url=gotham.url, actor=joker, event_type='Joined')
        EventFactory(
            object_url=penguin.url, referenced_object_url=gotham.url, actor=penguin, event_type='Joined')
        EventFactory(
            object_url=mario.url, referenced_object_url=gotham_gazette.url, actor=mario, event_type='Joined')

        response = self.client.get('/users/batman/events/?scopes=orgs')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            '/users/bruce/events/?scopes=orgs'
        )
        self.assertEqual(response.status_code, 200)
        # Bruce's orgs are Bat Cave (private), Wayne Corp and Gotham City
        # 1. Penguin's event of joining Gotham
        # 2. Joker's event of joining Gotham
        # 3. Alfred's event of joining Gotham
        # 4. Bruce's event of joining Gotham
        # 5. Bruce's event of joining Wayne Corp
        # 6. OCL's event of Creating Gotham City
        # 7. OCL's event of Creating Wayne Corp
        self.assertEqual(len(response.data), 7)
        self.assertEqual(response.data[0]['object']['url'], penguin.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[1]['object']['url'], joker.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], alfred.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[3]['object']['url'], bruce.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[4]['object']['url'], bruce.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], wayne_corp.url)
        self.assertEqual(response.data[5]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[5]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[6]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[6]['referenced_object']['url'], wayne_corp.url)

        response = self.client.get(
            '/users/bruce/events/?scopes=orgs',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )
        self.assertEqual(response.status_code, 200)
        # Bruce's orgs are Bat Cave (private), Wayne Corp and Gotham City
        # 1. Penguin's event of joining Gotham
        # 2. Joker's event of joining Gotham
        # 3. Alfred's event of joining Bat Cave
        # 4. Alfred's event of joining Gotham
        # 5. Bruce's event of joining Gotham
        # 6. Bruce's event of joining Wayne Corp
        # 7. Bruce's event of joining Bat Cave
        # 8. OCL's event of Creating Gotham City
        # 9. OCL's event of Creating Bat Cave
        # 10. OCL's event of Creating Wayne Corp
        self.assertEqual(len(response.data), 10)
        self.assertEqual(response.data[0]['object']['url'], penguin.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[1]['object']['url'], joker.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], alfred.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[3]['object']['url'], alfred.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[4]['object']['url'], bruce.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[5]['object']['url'], bruce.url)
        self.assertEqual(response.data[5]['referenced_object']['url'], wayne_corp.url)
        self.assertEqual(response.data[6]['object']['url'], bruce.url)
        self.assertEqual(response.data[6]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[7]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[7]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[8]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[8]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[9]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[9]['referenced_object']['url'], wayne_corp.url)

    def test_get_all_events(self):  # pylint:disable=too-many-statements,too-many-locals
        alfred = UserProfileFactory(username='alfred', first_name='Alfred', last_name='Pennyworth')
        bruce = UserProfileFactory(username='bruce', first_name='Bruce', last_name='Wayne')
        joker = UserProfileFactory(username='joker', first_name='Joker', last_name='The Clown')
        penguin = UserProfileFactory(username='penguin', first_name='Oswald', last_name='Cobblepot')
        mario = UserProfileFactory(username='mario', first_name='Mario', last_name='Ito')

        wayne_corp = OrganizationFactory(mnemonic='waynecorp', name='Wayne Enterprises')
        bat_cave = OrganizationFactory(mnemonic='batcave', name='Bat Cave', public_access='None')
        gotham = OrganizationFactory(mnemonic='gotham', name='Gotham City')
        gotham_gazette = OrganizationFactory(mnemonic='gotham_gazette', name='Gotham Gazette')
        gotham_gazette_crime_repo = OrganizationSourceFactory(
            mnemonic='gotham_gazette_crime', name='Gotham Gazette Crime Unit', organization=gotham_gazette,
            public_access='None')

        bruce.organizations.add(bat_cave)
        bruce.organizations.add(wayne_corp)
        bruce.organizations.add(gotham)
        alfred.organizations.add(bat_cave)
        alfred.organizations.add(gotham)
        joker.organizations.add(gotham)
        penguin.organizations.add(gotham)
        mario.organizations.add(gotham_gazette)

        alfred.follow(bruce)  # Creates Event
        joker.follow(bruce)  # Creates Event
        bruce.follow(joker)  # Creates Event
        bruce.follow(penguin)  # Creates Event
        bruce.follow(gotham_gazette)  # Creates Event
        bruce.follow(gotham_gazette_crime_repo)  # Creates Event

        EventFactory(
            object_url=bruce.url, referenced_object_url=bat_cave.url, actor=bruce, event_type='Joined', public=False)
        EventFactory(
            object_url=bruce.url, referenced_object_url=wayne_corp.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=bruce.url, referenced_object_url=gotham.url, actor=bruce, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=gotham.url, actor=alfred, event_type='Joined')
        EventFactory(
            object_url=alfred.url, referenced_object_url=bat_cave.url, actor=alfred, event_type='Joined', public=False)
        EventFactory(
            object_url=joker.url, referenced_object_url=gotham.url, actor=joker, event_type='Joined')
        EventFactory(
            object_url=penguin.url, referenced_object_url=gotham.url, actor=penguin, event_type='Joined')
        EventFactory(
            object_url=mario.url, referenced_object_url=gotham_gazette.url, actor=mario, event_type='Joined')

        response = self.client.get('/users/batman/events/?scopes=all')
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            '/users/bruce/events/?scopes=all'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 12)
        self.assertEqual(response.data[0]['object']['url'], mario.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[1]['object']['url'], penguin.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], joker.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[3]['object']['url'], alfred.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[4]['object']['url'], bruce.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[5]['object']['url'], bruce.url)
        self.assertEqual(response.data[5]['referenced_object']['url'], wayne_corp.url)
        self.assertEqual(response.data[6]['object']['url'], bruce.url)
        self.assertEqual(response.data[6]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[7]['object']['url'], bruce.url)
        self.assertEqual(response.data[7]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[8]['object']['url'], joker.url)
        self.assertEqual(response.data[8]['referenced_object']['url'], bruce.url)
        self.assertEqual(response.data[9]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[9]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[10]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[10]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[11]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[11]['referenced_object']['url'], wayne_corp.url)

        response = self.client.get(
            '/users/bruce/events/?scopes=all',  # all = orgs + following
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )
        self.assertEqual(response.status_code, 200)
        bruce_all_events = response.data
        self.assertEqual(len(bruce_all_events), 16)
        self.assertEqual(response.data[0]['object']['url'], mario.url)
        self.assertEqual(response.data[0]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[1]['object']['url'], penguin.url)
        self.assertEqual(response.data[1]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[2]['object']['url'], joker.url)
        self.assertEqual(response.data[2]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[3]['object']['url'], alfred.url)
        self.assertEqual(response.data[3]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[4]['object']['url'], alfred.url)
        self.assertEqual(response.data[4]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[5]['object']['url'], bruce.url)
        self.assertEqual(response.data[5]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[6]['object']['url'], bruce.url)
        self.assertEqual(response.data[6]['referenced_object']['url'], wayne_corp.url)
        self.assertEqual(response.data[7]['object']['url'], bruce.url)
        self.assertEqual(response.data[7]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[8]['object']['url'], bruce.url)
        self.assertEqual(response.data[8]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[9]['object']['url'], bruce.url)
        self.assertEqual(response.data[9]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[10]['object']['url'], joker.url)
        self.assertEqual(response.data[10]['referenced_object']['url'], bruce.url)
        self.assertEqual(response.data[11]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[11]['referenced_object']['url'], gotham_gazette_crime_repo.url)
        self.assertEqual(response.data[12]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[12]['referenced_object']['url'], gotham_gazette.url)
        self.assertEqual(response.data[13]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[13]['referenced_object']['url'], gotham.url)
        self.assertEqual(response.data[14]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[14]['referenced_object']['url'], bat_cave.url)
        self.assertEqual(response.data[15]['object']['url'], '/users/ocladmin/')
        self.assertEqual(response.data[15]['referenced_object']['url'], wayne_corp.url)

        response = self.client.get(
            '/users/bruce/events/?scopes=orgs,following',
            HTTP_AUTHORIZATION=f'Token {bruce.get_token()}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, bruce_all_events)
