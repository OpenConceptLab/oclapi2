from uuid import uuid4

from django.test import tag

from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.tests import OCLAPITestCase, OCLTestCase
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile

VERIFICATION = {'verified_at': '2026-11-20T14:02:11Z', 'process': 'ocl-verification-v1', 'hash': f'sha256:{"a" * 64}'}
FACTORIES = {Source: OrganizationSourceFactory, Collection: OrganizationCollectionFactory}


def make_version(head, version, **kwargs):
    """A version of the HEAD, with the same owner and name"""
    return FACTORIES[type(head)](
        mnemonic=head.mnemonic, organization=head.organization, name=head.name, version=version, **kwargs)


class RepoIsVerifiedTest(OCLTestCase):
    def test_a_repo_is_verified_while_its_latest_released_version_has_a_record(self):
        for model, factory in FACTORIES.items():
            with self.subTest(model=model.__name__):
                head = factory(organization=OrganizationFactory())
                self.assertFalse(head.is_verified)

                self.assertFalse(make_version(head, 'v0', verification=VERIFICATION).is_verified)  # not released
                self.assertFalse(head.is_verified)

                verified = make_version(head, 'v1', released=True, verification=VERIFICATION)
                self.assertTrue(verified.is_verified)
                self.assertTrue(head.is_verified)

                make_version(head, 'v2', released=True)  # a newer release carries no record
                self.assertFalse(head.is_verified)
                self.assertFalse(verified.is_verified)


@tag('es')
class RepoVerifiedSearchTest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.staff_token = UserProfile.objects.filter(is_superuser=True).first().get_token()
        self.organization = OrganizationFactory()
        self.token = f'zz{uuid4().hex[:12]}'  # the index outlives a test, so each run searches its own word

    def put_verification(self, version, record):
        response = self.client.put(
            version.uri, {'verification': record}, HTTP_AUTHORIZATION=f'Token {self.staff_token}', format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def verify(self, version):
        """What a staff member does: set the version's record."""
        self.put_verification(version, VERIFICATION)

    def clear_verification(self, version):
        """What a staff member does: clear the version's record."""
        self.put_verification(version, None)

    def get_ids(self, path, query, **params):
        response = self.client.get(path, {'q': query, 'limit': 50, **params})
        self.assertEqual(response.status_code, 200)
        return [item['id'] for item in response.json()]

    def make_repos(self):
        """
        Per model, a HEAD named as the token, one named the token plus a word, and a verified one that matches worse
        than both: the token in the middle of a long name.
        """
        repos = {}
        for model, factory in FACTORIES.items():
            key = model.__name__.lower()
            repos[key] = {
                'exact': factory(organization=self.organization, mnemonic=f'{key}-a', name=self.token),
                'prefix': factory(organization=self.organization, mnemonic=f'{key}-b', name=f'{self.token} copy'),
                'verified': factory(
                    organization=self.organization, mnemonic=f'{key}-c',
                    name=f'A long and distant {self.token} copy of it')
            }
            self.verify(make_version(repos[key]['verified'], 'v1', released=True))
        return repos

    def test_the_verified_repos_come_first(self):
        self.make_repos()

        self.assertEqual(self.get_ids('/sources/', self.token)[0], 'source-c')
        self.assertEqual(self.get_ids('/collections/', self.token)[0], 'collection-c')
        self.assertEqual(set(self.get_ids('/repos/', self.token)[:2]), {'source-c', 'collection-c'})

    def test_an_explicit_sort_is_kept(self):
        self.make_repos()

        self.assertEqual(
            self.get_ids('/sources/', self.token, sortDesc='name'), ['source-b', 'source-a', 'source-c'])

    def test_an_exact_id_match_ranks_above_the_verified_repos(self):
        for model, factory in FACTORIES.items():
            with self.subTest(model=model.__name__):
                key = model.__name__.lower()
                exact = factory(organization=self.organization, mnemonic=f'{self.token}-{key}', name='Other name')
                official = factory(
                    organization=self.organization, mnemonic=f'{self.token}-{key}-official', name='Official')
                self.verify(make_version(official, 'v1', released=True))

                self.assertEqual(self.get_ids('/repos/', exact.mnemonic)[:2], [exact.mnemonic, official.mnemonic])

    def test_verified_is_a_filter_and_a_facet(self):
        self.make_repos()
        verified = {'source-c', 'collection-c'}
        unverified = {'source-a', 'source-b', 'collection-a', 'collection-b'}

        for path in ('/sources/', '/collections/', '/repos/'):
            with self.subTest(path=path):
                found = {value: set(self.get_ids(path, self.token, verified=value)) for value in ('true', 'false')}
                everything = set(self.get_ids(path, self.token))

                self.assertEqual(found['true'], everything & verified)
                self.assertEqual(set(self.get_ids(path, self.token, verified='1')), found['true'])
                self.assertEqual(found['false'], everything & unverified)
                self.assertTrue(found['true'] and found['false'])

        response = self.client.get('/repos/', {'q': self.token, 'facetsOnly': 'true'})
        counts = {value: count for value, count, _ in response.json()['facets']['fields']['verified']}
        self.assertEqual(counts, {'true': 2, 'false': 4})

    def test_the_flag_follows_the_record_and_the_latest_release(self):
        for model, factory in FACTORIES.items():
            with self.subTest(model=model.__name__):
                document = SourceDocument if model is Source else CollectionDocument
                head = factory(organization=self.organization, mnemonic=f'{self.token}-{model.__name__.lower()}')
                version = make_version(head, 'v1', released=True)
                self.assertIs(document.get(id=head.id).verified, False)

                self.verify(version)
                self.assertIs(document.get(id=head.id).verified, True)

                self.clear_verification(version)
                self.assertIs(document.get(id=head.id).verified, False)

                self.verify(version)
                make_version(head, 'v2', released=True)
                self.assertIs(document.get(id=head.id).verified, False)
