from uuid import uuid4

from django.test import tag

from core.collections.documents import CollectionDocument
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.integration_tests.tests_repo_verification import VALID
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile

REPOS = {
    'source': (OrganizationSourceFactory, SourceDocument),
    'collection': (OrganizationCollectionFactory, CollectionDocument)
}


def make_version(factory, head, version, **kwargs):
    """A version of the HEAD, with the same owner and name"""
    return factory(mnemonic=head.mnemonic, organization=head.organization, name=head.name, version=version, **kwargs)


class RepoIsVerifiedTest(OCLTestCase):
    def test_a_repo_is_verified_by_the_record_of_its_latest_release_and_a_version_by_its_own(self):
        for key, (factory, _) in REPOS.items():
            with self.subTest(repo=key):
                head = factory(organization=OrganizationFactory())
                self.assertFalse(head.is_verified)

                self.assertTrue(make_version(factory, head, 'v0', verification=VALID).is_verified)
                self.assertFalse(head.is_verified)  # that version is not released

                verified = make_version(factory, head, 'v1', released=True, verification=VALID)
                self.assertTrue(head.is_verified)

                newer = make_version(factory, head, 'v2', released=True)  # a newer release carries no record
                self.assertFalse(head.is_verified)
                self.assertFalse(newer.is_verified)
                self.assertTrue(verified.is_verified)


@tag('es')
class RepoVerifiedSearchTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.staff_token = UserProfile.objects.filter(is_superuser=True).first().get_token()
        self.organization = OrganizationFactory()
        self.word = f'zz{uuid4().hex[:12]}'  # the index outlives a test, so each run searches its own word

    def set_verification(self, version, record):
        """What a staff member does: set the version's record, or clear it with None."""
        response = self.client.put(
            version.uri, {'verification': record}, HTTP_AUTHORIZATION=f'Token {self.staff_token}', format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def get_ids(self, path, **params):
        response = self.client.get(path, {'q': self.word, 'limit': 50, **params})
        self.assertEqual(response.status_code, 200)
        return [item['id'] for item in response.json()]

    def make_repos(self):
        """
        Per model, `<key>-a` named as the word, `<key>-b` named the word plus another, and the verified `<key>-c`
        that matches worse than both: the word in the middle of a long name.
        """
        for key, (factory, _) in REPOS.items():
            names = {'a': self.word, 'b': f'{self.word} copy', 'c': f'A long and distant {self.word}'}
            *_, verified = [
                factory(organization=self.organization, mnemonic=f'{key}-{suffix}', name=name)
                for suffix, name in names.items()
            ]
            self.set_verification(make_version(factory, verified, 'v1', released=True), VALID)

    def test_the_verified_repos_come_first(self):
        self.make_repos()

        self.assertEqual(self.get_ids('/sources/')[0], 'source-c')
        self.assertEqual(self.get_ids('/collections/')[0], 'collection-c')
        self.assertEqual(set(self.get_ids('/repos/')[:2]), {'source-c', 'collection-c'})

    def test_an_explicit_sort_is_kept(self):
        self.make_repos()

        self.assertEqual(self.get_ids('/sources/', sortDesc='name'), ['source-b', 'source-a', 'source-c'])

    def test_an_exact_id_match_ranks_above_the_verified_repos(self):
        for key, (factory, _) in REPOS.items():
            with self.subTest(repo=key):
                exact = factory(organization=self.organization, mnemonic=f'{self.word}-{key}', name='Other name')
                official = factory(organization=self.organization, mnemonic=f'{exact.mnemonic}-official', name='Name')
                self.set_verification(make_version(factory, official, 'v1', released=True), VALID)

                response = self.client.get('/repos/', {'q': exact.mnemonic})

                self.assertEqual([item['id'] for item in response.json()][:2], [exact.mnemonic, official.mnemonic])

    def test_verified_is_a_filter(self):
        self.make_repos()
        expected = {
            '/sources/': ({'source-c'}, {'source-a', 'source-b'}),
            '/collections/': ({'collection-c'}, {'collection-a', 'collection-b'}),
            '/repos/': ({'source-c', 'collection-c'}, {'source-a', 'source-b', 'collection-a', 'collection-b'}),
        }

        for path, (verified, unverified) in expected.items():
            with self.subTest(path=path):
                self.assertEqual(set(self.get_ids(path, verified='true')), verified)
                self.assertEqual(set(self.get_ids(path, verified='1')), verified)
                self.assertEqual(set(self.get_ids(path, verified='false')), unverified)

    def test_verified_is_a_facet(self):
        self.make_repos()

        response = self.client.get('/repos/', {'q': self.word, 'facetsOnly': 'true'})

        counts = {value: count for value, count, _ in response.json()['facets']['fields']['verified']}
        self.assertEqual(counts, {'true': 2, 'false': 4})

    def test_the_flag_follows_the_record_and_the_latest_release(self):
        for key, (factory, document) in REPOS.items():
            with self.subTest(repo=key):
                head = factory(organization=self.organization, mnemonic=f'{self.word}-{key}')
                version = make_version(factory, head, 'v1', released=True)
                self.assertIs(document.get(id=head.id).verified, False)

                self.set_verification(version, VALID)
                self.assertIs(document.get(id=head.id).verified, True)
                self.assertIs(document.get(id=version.id).verified, True)

                self.set_verification(version, None)
                self.assertIs(document.get(id=head.id).verified, False)

                self.set_verification(version, VALID)
                make_version(factory, head, 'v2', released=True)
                self.assertIs(document.get(id=head.id).verified, False)
                self.assertIs(document.get(id=version.id).verified, True)
