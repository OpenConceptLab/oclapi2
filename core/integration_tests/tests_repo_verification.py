from unittest.mock import patch, Mock

from core.collections.models import Collection
from core.collections.serializers import CollectionVersionExportSerializer, CollectionVersionDetailSerializer
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLAPITestCase
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.serializers import SourceVersionExportSerializer, SourceVersionDetailSerializer
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory

VALID = {'verified_at': '2026-11-20T14:02:11Z', 'process': 'ocl-verification-v1', 'hash': f'sha256:{"a" * 64}'}


class RepoVerificationTestsMixin:
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.first()
        self.staff_token = UserProfile.objects.filter(is_superuser=True).first().get_token()
        self.member = UserProfileFactory()
        self.organization.members.add(self.member)
        self.member_token = self.member.get_token()
        self.head = self.factory(organization=self.organization)
        self.version = self.factory(
            mnemonic=self.head.mnemonic, organization=self.organization, version='v1', released=True)

    def put(self, url, data, token):
        return self.client.put(url, data, HTTP_AUTHORIZATION=f'Token {token}', format='json')

    def stored(self):
        return self.model.objects.get(id=self.version.id).verification

    def test_staff_can_set_and_clear_and_it_is_returned(self):
        response = self.put(self.version.uri, {'verification': VALID}, self.staff_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['verification'], VALID)
        self.assertEqual(self.stored(), VALID)

        response = self.client.get(self.version.uri, HTTP_AUTHORIZATION=f'Token {self.staff_token}')
        self.assertEqual(response.data['verification'], VALID)

        response = self.client.get(f'{self.head.uri}versions/', HTTP_AUTHORIZATION=f'Token {self.staff_token}')
        self.assertEqual({item['id']: item['verification'] for item in response.data}['v1'], VALID)

        response = self.put(self.version.uri, {'verification': None}, self.staff_token)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.stored())

    def test_a_repository_admin_gets_403_and_can_still_edit_the_version(self):
        response = self.put(self.version.uri, {'verification': VALID}, self.member_token)

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.stored())

        response = self.put(self.version.uri, {'description': 'edited'}, self.member_token)

        self.assertEqual(response.status_code, 200)

    def test_a_repository_admin_cannot_clear_it(self):
        self.model.objects.filter(id=self.version.id).update(verification=VALID)

        response = self.put(self.version.uri, {'verification': None}, self.member_token)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored(), VALID)

    def get_body(self, url):
        return self.client.get(url, HTTP_AUTHORIZATION=f'Token {self.member_token}').json()

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_a_repository_admin_can_send_back_the_whole_version_they_read(self):
        for record in [None, VALID]:
            with self.subTest(record=record):
                self.model.objects.filter(id=self.version.id).update(verification=record)
                body = self.get_body(self.version.uri)
                self.assertEqual(body['verification'], record)

                response = self.put(self.version.uri, {**body, 'description': 'edited'}, self.member_token)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.stored(), record)
                self.assertEqual(self.model.objects.get(id=self.version.id).description, 'edited')

    def test_a_repository_admin_can_send_back_the_empty_record_head_returns(self):
        self.assertIsNone(self.get_body(f'{self.head.uri}HEAD/')['verification'])

        response = self.put(
            f'{self.head.uri}HEAD/', {'description': 'edited', 'verification': None}, self.member_token)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.model.objects.get(id=self.head.id).verification)

    def test_a_repository_admin_cannot_change_it_inside_a_whole_version(self):
        self.model.objects.filter(id=self.version.id).update(verification=VALID)
        body = self.get_body(self.version.uri)
        other = {**VALID, 'process': 'ocl-verification-v2'}

        for record in [other, None]:
            with self.subTest(record=record):
                response = self.put(
                    self.version.uri, {**body, 'description': 'edited', 'verification': record}, self.member_token)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.stored(), VALID)
                self.assertNotEqual(self.model.objects.get(id=self.version.id).description, 'edited')

    def test_sending_only_the_unchanged_record_saves_nothing(self):
        self.model.objects.filter(id=self.version.id).update(verification=VALID)
        before = self.model.objects.get(id=self.version.id)

        for token in [self.member_token, self.staff_token]:
            response = self.put(self.version.uri, {'verification': dict(reversed(VALID.items()))}, token)

            self.assertEqual(response.status_code, 200)
            after = self.model.objects.get(id=self.version.id)
            self.assertEqual(after.verification, VALID)
            self.assertEqual(after.updated_at, before.updated_at)

    @patch('core.sources.models.index_source_concepts', Mock(__name__='index_source_concepts'))
    @patch('core.sources.models.index_source_mappings', Mock(__name__='index_source_mappings'))
    def test_an_empty_record_is_accepted_when_creating_a_version(self):
        data = {'id': 'v2', 'description': 'new', 'verification': None}

        response = self.client.post(
            f'{self.head.uri}versions/', data, HTTP_AUTHORIZATION=f'Token {self.member_token}', format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(self.model.objects.get(mnemonic=self.head.mnemonic, version='v2').verification)

    def test_a_repository_admin_cannot_set_it_through_the_latest_version_route(self):
        response = self.put(f'{self.head.uri}latest/', {'verification': VALID}, self.member_token)

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.stored())

    def test_a_repository_admin_cannot_set_it_through_extras(self):
        response = self.put(self.version.uri, {'extras': {'verification': VALID}}, self.member_token)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.stored())

        response = self.put(f'{self.head.uri}extras/verification/', {'verification': VALID}, self.member_token)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.model.objects.get(id=self.head.id).verification)

    def test_it_is_rejected_on_head(self):
        response = self.put(self.head.uri, {'verification': VALID}, self.staff_token)
        self.assertEqual(response.status_code, 400)
        self.assertIn('verification', response.data)

        response = self.put(self.head.uri, {'verification': VALID}, self.member_token)
        self.assertEqual(response.status_code, 403)

        self.assertIsNone(self.model.objects.get(id=self.head.id).verification)

    def test_it_is_rejected_when_creating_a_version(self):
        data = {'id': 'v2', 'description': 'new', 'verification': VALID}
        url = f'{self.head.uri}versions/'

        response = self.client.post(url, data, HTTP_AUTHORIZATION=f'Token {self.staff_token}', format='json')
        self.assertEqual(response.status_code, 400)

        response = self.client.post(url, data, HTTP_AUTHORIZATION=f'Token {self.member_token}', format='json')
        self.assertEqual(response.status_code, 403)

        self.assertFalse(self.model.objects.filter(mnemonic=self.head.mnemonic, version='v2').exists())

    def test_the_shape_is_checked(self):
        for invalid in [
                'text', [], {}, 1,
                {**VALID, 'extra': 'x'},
                {key: value for key, value in VALID.items() if key != 'hash'},
                {**VALID, 'verified_at': '2026-11-20'},
                {**VALID, 'verified_at': '2026-13-20T14:02:11Z'},
                {**VALID, 'verified_at': '2026-11-20T14:02:11+00:60'},
                {**VALID, 'verified_at': '2026-11-20T14:02:11+24:00'},
                {**VALID, 'verified_at': '2026-11-20T14:02:11Z\n'},
                {**VALID, 'verified_at': '2026-11-20T14:02:11'},
                {**VALID, 'verified_at': '2026-11-20T14:02Z'},
                {**VALID, 'verified_at': '2026-11-20T14:02'},
                {**VALID, 'verified_at': 20261120},
                {**VALID, 'process': 'ocl verification'},
                {**VALID, 'process': 'ocl_verification'},
                {**VALID, 'process': ''},
                {**VALID, 'process': 'a' * 65},
                {**VALID, 'hash': 'a' * 64},
                {**VALID, 'hash': f'sha256:{"A" * 64}'},
                {**VALID, 'hash': f'sha256:{"a" * 63}'},
                {**VALID, 'hash': f'sha256:{"a" * 64}\n'},
        ]:
            with self.subTest(invalid=invalid):
                response = self.put(self.version.uri, {'verification': invalid}, self.staff_token)

                self.assertEqual(response.status_code, 400)
                self.assertIn('verification', response.data)
                self.assertIsNone(self.stored())

    def test_it_accepts_fractional_seconds_and_any_offset(self):
        for verified_at in ['2026-11-20T14:02:11.1234567Z', '2026-11-20T14:02:11-03:00']:
            with self.subTest(verified_at=verified_at):
                response = self.put(
                    self.version.uri, {'verification': {**VALID, 'verified_at': verified_at}}, self.staff_token)

                self.assertEqual(response.status_code, 200)

    def test_a_process_of_the_longest_allowed_length_is_accepted(self):
        response = self.put(self.version.uri, {'verification': {**VALID, 'process': 'a' * 64}}, self.staff_token)

        self.assertEqual(response.status_code, 200)

    def test_it_is_not_copied_to_a_new_version(self):
        self.head.verification = VALID
        new_version = self.model(mnemonic=self.head.mnemonic, version='v2', verification=VALID)

        new_version.update_version_data(self.head)

        self.assertIsNone(new_version.verification)

    def test_head_never_keeps_it_when_saved(self):
        self.head.verification = VALID

        self.head.full_clean()

        self.assertIsNone(self.head.verification)

    def test_saving_only_the_record_changes_nothing_else_on_the_version(self):
        self.model.objects.filter(id=self.version.id).update(revision_date=None)
        self.model.objects.get(id=self.version.id).get_checksums(recalculate=False)  # the update view fills these once
        before = self.model.objects.get(id=self.version.id)
        export_before = dict(self.export_serializer(before).data)

        response = self.put(self.version.uri, {'verification': VALID}, self.staff_token)

        self.assertEqual(response.status_code, 200)
        after = self.model.objects.get(id=self.version.id)
        self.assertEqual(after.verification, VALID)
        self.assertIsNone(after.revision_date)
        self.assertEqual(after.updated_at, before.updated_at)
        self.assertEqual(after.updated_by_id, before.updated_by_id)
        self.assertEqual(dict(self.export_serializer(after).data), export_before)

    def test_the_repository_route_for_a_version_also_saves_only_the_record(self):
        stale = {'standard': 'stale', 'smart': 'stale'}
        self.model.objects.filter(id=self.version.id).update(checksums=stale)
        before = self.model.objects.get(id=self.version.id)
        url = f'{self.head.uri}?version=v1'

        for token in [self.staff_token, self.member_token]:
            response = self.put(url, {'verification': VALID}, token)

            self.assertEqual(response.status_code, 200)
            after = self.model.objects.get(id=self.version.id)
            self.assertEqual(after.verification, VALID)
            self.assertEqual(after.updated_at, before.updated_at)
            self.assertEqual(after.checksums, stale)

        response = self.put(url, {'description': 'edited'}, self.member_token)

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.model.objects.get(id=self.version.id).checksums, stale)

    def test_a_nested_serializer_without_a_payload_skips_the_check(self):
        self.assertEqual(self.detail_serializer().validate({'name': 'nested'}), {'name': 'nested'})

    def test_it_is_left_out_of_the_export(self):
        self.version.verification = VALID

        self.assertEqual(self.detail_serializer(self.version).data['verification'], VALID)
        self.assertNotIn('verification', self.export_serializer(self.version).data)


class SourceVerificationTest(RepoVerificationTestsMixin, OCLAPITestCase):
    factory = OrganizationSourceFactory
    model = Source
    detail_serializer = SourceVersionDetailSerializer
    export_serializer = SourceVersionExportSerializer


class CollectionVerificationTest(RepoVerificationTestsMixin, OCLAPITestCase):
    factory = OrganizationCollectionFactory
    model = Collection
    detail_serializer = CollectionVersionDetailSerializer
    export_serializer = CollectionVersionExportSerializer
