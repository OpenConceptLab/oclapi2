"""
The shared canonical resolver (ConceptContainerModel.resolve_reference_expression) with the URL registry toggle off
and on: today's answers for its existing callers, the flags FHIR resolves with (repo_type, allow_head), reads that
never write a registry entry, and $resolveReference's ?namespace=. OpenConceptLab/ocl_online#451.
"""
from mock import patch

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import HEAD
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.toggles.models import Toggle
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.models import UserProfile

REGISTRY_TOGGLE = 'URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE'
ICD10 = 'http://hl7.org/fhir/sid/icd-10'
PBD = 'http://pbd.com.np'
DRAFT = 'https://example.org/draft'


def set_registry_toggle(on):
    """The toggle in every environment's column, so a test doesn't depend on which one settings.ENV reads."""
    Toggle.objects.filter(name=REGISTRY_TOGGLE).update(
        dev=on, qa=on, demo=on, staging=on, who_staging=on, production=on)
    assert Toggle.get(REGISTRY_TOGGLE) is on


def source(org, mnemonic, version=HEAD, **kwargs):
    return OrganizationSourceFactory(organization=org, mnemonic=mnemonic, version=version, **kwargs)


def collection(org, mnemonic, version=HEAD, **kwargs):
    return OrganizationCollectionFactory(organization=org, mnemonic=mnemonic, version=version, **kwargs)


class ResolverCorpusMixin:
    """
    ICD-10 shared by WHO (first, so the lower id) and sehatapp, with WHO's released version row lacking the
    canonical as in production; a Source and a Collection sharing a canonical in one org (padam); a repository with
    no released version.
    """
    def setUp(self):
        super().setUp()
        self.who = OrganizationFactory(mnemonic='WHO')
        self.sehatapp = OrganizationFactory(mnemonic='sehatapp')
        self.padam = OrganizationFactory(mnemonic='padam')
        self.drafts = OrganizationFactory(mnemonic='drafts')

        self.who_icd = source(self.who, 'ICD-10-WHO', canonical_url=ICD10)
        self.who_icd_2019 = source(self.who, 'ICD-10-WHO', 'ICD102019', released=True, canonical_url=None)
        self.sehat_icd = source(self.sehatapp, 'ICD-10', canonical_url=ICD10)
        self.sehat_icd_v1 = source(self.sehatapp, 'ICD-10', 'v1.0', released=True, canonical_url=ICD10)

        self.pbd_source = source(self.padam, 'pbd', canonical_url=PBD)
        self.pbd_source_v1 = source(self.padam, 'pbd', 'v1', released=True, canonical_url=PBD)
        self.pbd_collection = collection(self.padam, 'pbd-vs', canonical_url=PBD)
        self.pbd_collection_v1 = collection(self.padam, 'pbd-vs', 'v1', released=True, canonical_url=PBD)

        self.draft = source(self.drafts, 'draft', canonical_url=DRAFT)


class ResolverToggleOffTest(ResolverCorpusMixin, OCLTestCase):
    """No registry: the lowest-id active repository with the canonical, Source before Collection."""
    def setUp(self):
        set_registry_toggle(False)
        super().setUp()

    def test_defaults_keep_todays_answers(self):
        version, entry = Source.resolve_reference_expression(ICD10)
        self.assertEqual(version.id, self.who_icd_2019.id)
        self.assertEqual(version.canonical_url, ICD10)  # filled in from the request, as before
        self.assertTrue(version.is_fqdn)
        self.assertIsNone(entry)

        # a namespace limits the match to that owner's repositories
        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/')
        self.assertEqual(version.id, self.sehat_icd_v1.id)
        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/padam/')
        self.assertIsNone(version.id)

        # type-blind: a Source wins over a Collection with the same canonical, whatever class asks
        version, _ = Collection.resolve_reference_expression(PBD)
        self.assertEqual(version.id, self.pbd_source_v1.id)
        self.assertIsInstance(version, Source)

        # nothing released: HEAD
        version, _ = Source.resolve_reference_expression(DRAFT)
        self.assertEqual(version.id, self.draft.id)

        # a version row's own canonical matches, and is returned as it is
        self.sehat_icd.canonical_url = 'http://example.org/new-icd'
        self.sehat_icd.save()
        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/')
        self.assertEqual(version.id, self.sehat_icd_v1.id)

        # unknown: an unsaved Source
        version, _ = Collection.resolve_reference_expression('http://example.org/unknown')
        self.assertIsNone(version.id)
        self.assertIsInstance(version, Source)

    def test_repo_type_resolves_only_to_that_type(self):
        version, _ = Source.resolve_reference_expression(PBD, repo_type=Collection)
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertIsInstance(version, Collection)

        version, _ = Source.resolve_reference_expression(PBD, repo_type=Source)
        self.assertEqual(version.id, self.pbd_source_v1.id)

        version, _ = Source.resolve_reference_expression(ICD10, repo_type=Collection)
        self.assertIsNone(version.id)
        self.assertIsInstance(version, Collection)

        version, _ = Source.resolve_reference_expression(self.pbd_collection.uri, repo_type=Source)
        self.assertIsNone(version.id)
        version, _ = Source.resolve_reference_expression(self.pbd_collection.uri, repo_type=Collection)
        self.assertEqual(version.id, self.pbd_collection_v1.id)

    def test_repo_type_matches_the_repository_canonical_on_its_head(self):
        self.sehat_icd.canonical_url = 'http://example.org/new-icd'
        self.sehat_icd.save()

        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', repo_type=Source)
        self.assertIsNone(version.id)

        version, _ = Source.resolve_reference_expression(
            'http://example.org/new-icd', '/orgs/sehatapp/', repo_type=Source)
        self.assertEqual(version.id, self.sehat_icd_v1.id)

    def test_repo_type_serves_only_active_versions(self):
        self.sehat_icd_v1.is_active = False
        self.sehat_icd_v1.save()

        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', 'v1.0')
        self.assertEqual(version.id, self.sehat_icd_v1.id)

        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', 'v1.0', repo_type=Source)
        self.assertIsNone(version.id)

    def test_allow_head_false_never_falls_back_to_head(self):
        version, _ = Source.resolve_reference_expression(DRAFT, allow_head=False)
        self.assertIsNone(version.id)
        self.assertTrue(version.is_fqdn)

        version, _ = Source.resolve_reference_expression(DRAFT, version=HEAD, allow_head=False)
        self.assertEqual(version.id, self.draft.id)

        version, _ = Source.resolve_reference_expression(f'{DRAFT}|HEAD', allow_head=False)
        self.assertEqual(version.id, self.draft.id)

        version, _ = Source.resolve_reference_expression(ICD10, allow_head=False)
        self.assertEqual(version.id, self.who_icd_2019.id)


class ResolverToggleOnTest(ResolverCorpusMixin, OCLTestCase):
    """The registry's order: the owner's entry, the owner's repositories, the global entry; nothing else."""
    def setUp(self):
        set_registry_toggle(True)
        super().setUp()
        self.icd_entry = GlobalURLRegistryFactory(url=ICD10, namespace=self.who.uri)
        self.pbd_entry = GlobalURLRegistryFactory(url=PBD, namespace=self.padam.uri)
        self.draft_entry = GlobalURLRegistryFactory(url=DRAFT, namespace=self.drafts.uri)

    def test_defaults_keep_todays_answers(self):
        version, entry = Source.resolve_reference_expression(ICD10)
        self.assertEqual(version.id, self.who_icd_2019.id)
        self.assertEqual(entry, self.icd_entry)

        version, entry = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/')
        self.assertEqual(version.id, self.sehat_icd_v1.id)
        self.assertIsNone(entry)

        version, entry = Collection.resolve_reference_expression(PBD)
        self.assertEqual(version.id, self.pbd_source_v1.id)
        self.assertEqual(entry, self.pbd_entry)

        version, _ = Source.resolve_reference_expression(DRAFT)
        self.assertEqual(version.id, self.draft.id)

        version, entry = Source.resolve_reference_expression('http://example.org/unregistered')
        self.assertIsNone(version.id)
        self.assertIsNone(entry)

    def test_repo_type_finds_that_type_in_the_entry_namespace(self):
        version, entry = Source.resolve_reference_expression(PBD, repo_type=Collection)
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertEqual(entry, self.pbd_entry)

        version, entry = Source.resolve_reference_expression(PBD, repo_type=Source)
        self.assertEqual(version.id, self.pbd_source_v1.id)
        self.assertEqual(entry, self.pbd_entry)

    def test_an_entry_without_a_repository_of_the_type_is_unresolved_without_falling_through(self):
        collection(self.sehatapp, 'icd-vs', canonical_url=ICD10)
        collection(self.sehatapp, 'icd-vs', 'v1', released=True, canonical_url=ICD10)

        version, entry = Source.resolve_reference_expression(ICD10, repo_type=Collection)
        self.assertIsNone(version.id)
        self.assertIsInstance(version, Collection)
        self.assertEqual(entry, self.icd_entry)

    def test_an_owner_entry_resolves_by_type_in_its_namespace(self):
        owner_entry = OrganizationURLRegistryFactory(organization=self.sehatapp, url=PBD, namespace=self.padam.uri)

        version, entry = Source.resolve_reference_expression(PBD, '/orgs/sehatapp/', repo_type=Collection)
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertEqual(entry, owner_entry)

        version, entry = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', repo_type=Source)
        self.assertEqual(version.id, self.sehat_icd_v1.id)
        self.assertIsNone(entry)

    def test_repo_type_skips_a_deleted_repository(self):
        self.who_icd.is_active = False
        self.who_icd.save()

        version, _ = Source.resolve_reference_expression(ICD10)
        self.assertEqual(version.id, self.who_icd_2019.id)

        version, entry = Source.resolve_reference_expression(ICD10, repo_type=Source)
        self.assertIsNone(version.id)
        self.assertEqual(entry, self.icd_entry)

    def test_allow_head_false_never_falls_back_to_head(self):
        version, entry = Source.resolve_reference_expression(DRAFT, repo_type=Source, allow_head=False)
        self.assertIsNone(version.id)
        self.assertEqual(entry, self.draft_entry)

        version, _ = Source.resolve_reference_expression(DRAFT, version=HEAD, repo_type=Source, allow_head=False)
        self.assertEqual(version.id, self.draft.id)

    def test_resolving_never_writes_a_registry_entry(self):
        OrganizationURLRegistryFactory(organization=self.sehatapp, url=PBD, namespace=self.padam.uri)

        with patch.object(URLRegistry, 'save') as save_mock:
            Source.resolve_reference_expression(ICD10)
            Source.resolve_reference_expression(PBD, '/orgs/sehatapp/')
            Source.resolve_reference_expression(PBD, repo_type=Collection)

        save_mock.assert_not_called()
        self.icd_entry.refresh_from_db()
        self.assertIsNone(self.icd_entry.repo_id)


class URLRegistryReadsTest(ResolverCorpusMixin, OCLTestCase):
    def setUp(self):
        super().setUp()
        self.entry = GlobalURLRegistryFactory(url=PBD, namespace=self.padam.uri)

    def test_lookup_reads_without_writing(self):
        with patch.object(URLRegistry, 'save') as save_mock:
            repo, entry = URLRegistry.lookup(PBD)

        save_mock.assert_not_called()
        self.assertEqual(repo, self.pbd_source)
        self.assertEqual(entry, self.entry)

    def test_find_repo(self):
        self.assertEqual(self.entry.find_repo(), self.pbd_source)
        self.assertEqual(self.entry.find_repo(Source), self.pbd_source)
        self.assertEqual(self.entry.find_repo(Collection), self.pbd_collection)
        self.assertIsNone(URLRegistry(url=PBD).find_repo())

    def test_lookup_entry_still_keeps_the_repository_on_the_entry(self):
        """The write path (an entry's create or update, a canonical URL change) is unchanged"""
        with patch.object(URLRegistry, 'save', wraps=self.entry.save) as save_mock:
            self.assertEqual(self.entry.lookup_entry(), self.pbd_source)
            self.assertEqual(self.entry.lookup_entry(), self.pbd_source)

        self.assertEqual(save_mock.call_count, 2)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.repo, self.pbd_source)

        with patch.object(URLRegistry, 'save') as save_mock:
            self.assertIsNone(URLRegistry(url='http://example.org/nowhere', namespace=self.padam.uri).lookup_entry())
        save_mock.assert_not_called()


class ResolveReferenceNamespaceTest(OCLAPITestCase):
    """$resolveReference's ?namespace=: the namespace of every expression that doesn't name its own."""
    def setUp(self):
        super().setUp()
        self.token = UserProfile.objects.get(username='ocladmin').get_token()
        self.org_a = OrganizationFactory(mnemonic='org-a')
        self.org_b = OrganizationFactory(mnemonic='org-b')
        self.source_a = source(self.org_a, 'codes', canonical_url=ICD10)
        self.source_a_v1 = source(self.org_a, 'codes', 'v1', released=True, canonical_url=ICD10)
        self.source_b = source(self.org_b, 'codes', canonical_url=ICD10)
        self.source_b_v1 = source(self.org_b, 'codes', 'v1', released=True, canonical_url=ICD10)
        GlobalURLRegistryFactory(url=ICD10, namespace=self.org_a.uri)

    def resolve(self, expressions, query=''):
        response = self.client.post(
            f'/$resolveReference/{query}', expressions, HTTP_AUTHORIZATION='Token ' + self.token, format='json')
        self.assertEqual(response.status_code, 200)
        return [item['result']['version_url'] if item['resolved'] else None for item in response.data]

    def test_with_the_registry(self):
        set_registry_toggle(True)
        a_v1, b_v1 = self.source_a_v1.uri, self.source_b_v1.uri

        self.assertEqual(self.resolve([{'url': ICD10}]), [a_v1])
        self.assertEqual(self.resolve([{'url': ICD10}, ICD10], '?namespace=/orgs/org-b/'), [b_v1, b_v1])
        self.assertEqual(
            self.resolve([{'url': ICD10, 'namespace': '/orgs/org-a/'}, {'url': ICD10, 'namespace': None}],
                         '?namespace=/orgs/org-b/'),
            [a_v1, a_v1])
        self.assertEqual(self.resolve([{'url': ICD10}], '?namespace='), [a_v1])

    def test_one_expression_query_count(self):
        """
        A hot path: these counts may only fall. Without the registry: the token (3), the toggle, the repository, its
        latest released version and the serializer (2); ?namespace= adds a toggle read. The registry adds the global
        entry, its namespace owner and a Source check before the read; there ?namespace= also swaps the global entry
        and its owner for the namespace owner and its own entry.
        """
        for registry, query, count in ((False, '', 8), (False, '?namespace=/orgs/org-b/', 9),
                                       (True, '', 11), (True, '?namespace=/orgs/org-b/', 12)):
            with self.subTest(registry=registry, query=query):
                set_registry_toggle(registry)
                with self.assertNumQueries(count):
                    self.resolve([{'url': ICD10}], query)

    def test_without_the_registry_the_parameter_is_not_used(self):
        set_registry_toggle(False)
        a_v1, b_v1 = self.source_a_v1.uri, self.source_b_v1.uri

        self.assertEqual(self.resolve([{'url': ICD10}], '?namespace=/orgs/org-b/'), [a_v1])
        self.assertEqual(self.resolve([{'url': ICD10}], '?namespace=/orgs/nobody/'), [a_v1])
        self.assertEqual(self.resolve([{'url': ICD10, 'namespace': '/orgs/org-b/'}], '?namespace=/orgs/org-a/'), [b_v1])
