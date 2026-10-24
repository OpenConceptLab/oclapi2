"""
The shared canonical resolver (ConceptContainerModel.resolve_reference_expression) with the URL registry toggle off
and on: today's answers for its existing callers, the rules FHIR resolves with, reads that
never write a registry entry, the registry links kept on the write side instead, and $resolveReference's ?namespace=.
OpenConceptLab/ocl_online#451.
"""
from contextlib import contextmanager

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from mock import patch
from pydash import get

from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import HEAD
from core.common.models import ConceptContainerModel
from core.common.tasks import resolve_url_registry_entries
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.fhir.resolve import FhirResolution
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.toggles.models import Toggle
from core.url_registry.constants import URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE
from core.url_registry.documents import URLRegistryDocument
from core.url_registry.factories import GlobalURLRegistryFactory, OrganizationURLRegistryFactory
from core.url_registry.models import URLRegistry
from core.users.models import UserProfile

ICD10 = 'http://hl7.org/fhir/sid/icd-10'
PBD = 'http://pbd.com.np'
DRAFT = 'https://example.org/draft'
FLAGGED = 'https://example.org/flagged-head'


def set_registry_toggle(on):
    """The toggle in every environment's column, so a test doesn't depend on which one settings.ENV reads."""
    Toggle.objects.filter(name=URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE).update(
        dev=on, qa=on, demo=on, staging=on, who_staging=on, production=on)
    assert Toggle.get(URL_REGISTRY_IN_RESOLVE_REFERENCE_TOGGLE) is on


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


class StrictDefaultCases:
    """With the toggle off and on (the toggle classes mix this in)."""
    def test_a_head_flagged_released_is_never_the_strict_default(self):
        """FHIR skips HEAD as the default even when flagged released; the shared default still serves it."""
        head = source(self.drafts, 'flagged', released=True, canonical_url=FLAGGED)
        GlobalURLRegistryFactory(url=FLAGGED, namespace=self.drafts.uri)  # read only with the toggle on

        version, _ = Source.resolve_reference_expression(FLAGGED)
        self.assertEqual(version.id, head.id)
        version, _ = Source.resolve_reference_expression(FLAGGED, resolution=FhirResolution(Source))
        self.assertIsNone(version.id)
        self.assertTrue(version.is_fqdn)
        version, _ = Source.resolve_reference_expression(
            FLAGGED, version=HEAD, resolution=FhirResolution(Source))
        self.assertEqual(version.id, head.id)

        released = source(self.drafts, 'flagged', 'v1', released=True, canonical_url=FLAGGED)
        version, _ = Source.resolve_reference_expression(FLAGGED, resolution=FhirResolution(Source))
        self.assertEqual(version.id, released.id)


class ResolverToggleOffTest(StrictDefaultCases, ResolverCorpusMixin, OCLTestCase):
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
        version, _ = Source.resolve_reference_expression(PBD, resolution=FhirResolution(Collection))
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertIsInstance(version, Collection)

        version, _ = Source.resolve_reference_expression(PBD, resolution=FhirResolution(Source))
        self.assertEqual(version.id, self.pbd_source_v1.id)

        version, _ = Source.resolve_reference_expression(ICD10, resolution=FhirResolution(Collection))
        self.assertIsNone(version.id)
        self.assertIsInstance(version, Collection)

        version, _ = Source.resolve_reference_expression(self.pbd_collection.uri, resolution=FhirResolution(Source))
        self.assertIsNone(version.id)
        version, _ = Source.resolve_reference_expression(self.pbd_collection.uri, resolution=FhirResolution(Collection))
        self.assertEqual(version.id, self.pbd_collection_v1.id)

    def test_repo_type_matches_the_repository_canonical_on_its_head(self):
        self.sehat_icd.canonical_url = 'http://example.org/new-icd'
        self.sehat_icd.save()

        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', resolution=FhirResolution(Source))
        self.assertIsNone(version.id)

        version, _ = Source.resolve_reference_expression(
            'http://example.org/new-icd', '/orgs/sehatapp/', resolution=FhirResolution(Source))
        self.assertEqual(version.id, self.sehat_icd_v1.id)

    def test_repo_type_serves_only_active_versions(self):
        self.sehat_icd_v1.is_active = False
        self.sehat_icd_v1.save()

        version, _ = Source.resolve_reference_expression(ICD10, '/orgs/sehatapp/', 'v1.0')
        self.assertEqual(version.id, self.sehat_icd_v1.id)

        version, _ = Source.resolve_reference_expression(
            ICD10, '/orgs/sehatapp/', 'v1.0', resolution=FhirResolution(Source))
        self.assertIsNone(version.id)


class ResolverToggleOnTest(StrictDefaultCases, ResolverCorpusMixin, OCLTestCase):
    """The registry's order: the owner's entry, the owner's repositories, the global entry; nothing else."""
    def setUp(self):
        set_registry_toggle(True)
        super().setUp()
        self.icd_entry = GlobalURLRegistryFactory(url=ICD10, namespace=self.who.uri)
        self.pbd_entry = GlobalURLRegistryFactory(url=PBD, namespace=self.padam.uri)
        GlobalURLRegistryFactory(url=DRAFT, namespace=self.drafts.uri)

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
        version, entry = Source.resolve_reference_expression(PBD, resolution=FhirResolution(Collection))
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertEqual(entry, self.pbd_entry)

        version, entry = Source.resolve_reference_expression(PBD, resolution=FhirResolution(Source))
        self.assertEqual(version.id, self.pbd_source_v1.id)
        self.assertEqual(entry, self.pbd_entry)

    def test_an_entry_without_a_repository_of_the_type_is_unresolved_without_falling_through(self):
        collection(self.sehatapp, 'icd-vs', canonical_url=ICD10)
        collection(self.sehatapp, 'icd-vs', 'v1', released=True, canonical_url=ICD10)

        version, entry = Source.resolve_reference_expression(ICD10, resolution=FhirResolution(Collection))
        self.assertIsNone(version.id)
        self.assertIsInstance(version, Collection)
        self.assertEqual(entry, self.icd_entry)

    def test_an_owner_entry_resolves_by_type_in_its_namespace(self):
        owner_entry = OrganizationURLRegistryFactory(organization=self.sehatapp, url=PBD, namespace=self.padam.uri)

        version, entry = Source.resolve_reference_expression(
            PBD, '/orgs/sehatapp/', resolution=FhirResolution(Collection))
        self.assertEqual(version.id, self.pbd_collection_v1.id)
        self.assertEqual(entry, owner_entry)

        version, entry = Source.resolve_reference_expression(
            ICD10, '/orgs/sehatapp/', resolution=FhirResolution(Source))
        self.assertEqual(version.id, self.sehat_icd_v1.id)
        self.assertIsNone(entry)

    def test_repo_type_skips_a_deleted_repository(self):
        self.who_icd.is_active = False
        self.who_icd.save()

        version, _ = Source.resolve_reference_expression(ICD10)
        self.assertEqual(version.id, self.who_icd_2019.id)

        version, entry = Source.resolve_reference_expression(ICD10, resolution=FhirResolution(Source))
        self.assertIsNone(version.id)
        self.assertEqual(entry, self.icd_entry)

    def test_resolving_never_writes_a_registry_entry(self):
        OrganizationURLRegistryFactory(organization=self.sehatapp, url=PBD, namespace=self.padam.uri)

        with patch.object(URLRegistry, 'save') as save_mock:
            Source.resolve_reference_expression(ICD10)
            Source.resolve_reference_expression(PBD, '/orgs/sehatapp/')
            Source.resolve_reference_expression(PBD, resolution=FhirResolution(Collection))

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
        self.assertEqual(FhirResolution(Source).entry_repository(self.entry), self.pbd_source)
        self.assertEqual(FhirResolution(Collection).entry_repository(self.entry), self.pbd_collection)
        self.assertIsNone(URLRegistry(url=PBD).find_repo())

    def test_lookup_entry_keeps_the_link_and_saves_only_on_a_change(self):
        """The write path: lookup_entry links the repository found, clears a link that no longer resolves, and saves
        (so reindexes) only when the link changes"""
        with patch.object(URLRegistry, 'save', wraps=self.entry.save) as save_mock:
            self.assertEqual(self.entry.lookup_entry(), self.pbd_source)
            self.assertEqual(self.entry.lookup_entry(), self.pbd_source)
        self.assertEqual(save_mock.call_count, 1)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.repo, self.pbd_source)

        self.entry.namespace = self.drafts.uri
        self.entry.save()  # as the entry's update does, before its lookup_entry
        self.assertIsNone(self.entry.lookup_entry())
        self.entry.refresh_from_db()
        self.assertEqual(
            (self.entry.namespace, self.entry.repo_id, self.entry.repo_type_id), (self.drafts.uri, None, None))

        with patch.object(URLRegistry, 'save') as save_mock:
            self.assertIsNone(URLRegistry(url='http://example.org/nowhere', namespace=self.padam.uri).lookup_entry())
        save_mock.assert_not_called()


def run_inline(args, **_):
    """resolve_url_registry_entries.apply_async, run in the test's own transaction (tests have no broker)"""
    return resolve_url_registry_entries(*args)


@contextmanager
def queued_tasks_run(test):
    """The resolve_url_registry_entries tasks a block queues on commit, run inline when it ends; yields their mock"""
    with patch.object(resolve_url_registry_entries, 'apply_async', side_effect=run_inline) as queued, \
            test.captureOnCommitCallbacks(execute=True):
        yield queued


@override_settings(ES_SYNC=True, ELASTICSEARCH_DSL_AUTOSYNC=True, ELASTICSEARCH_DSL_AUTO_REFRESH=True)
class URLRegistryLinkTest(OCLAPITestCase):
    """
    An entry's cached repository is kept on the write side, since reads no longer write: a repository created after
    its entry links it, and a canonical URL change clears the entries of the old URL and fills those of the new one,
    in the registry API and in the registry's search index. The resolve_url_registry_entries task does it, queued once
    the change commits. Indexing is switched on here because a test that pauses
    it leaves it paused (resume_indexing does nothing in TEST_MODE).
    """
    OLD, NEW = 'https://example.org/acme/codes', 'https://example.org/acme/codes-v2'

    def setUp(self):
        super().setUp()
        self.auth = {'HTTP_AUTHORIZATION': 'Token ' + UserProfile.objects.get(username='ocladmin').get_token()}
        self.org = OrganizationFactory(mnemonic='acme-registry')

    def create_entry(self, base, url):
        response = self.client.post(
            f'{base}url-registry/', {'name': url, 'url': url, 'namespace': self.org.uri}, format='json', **self.auth)
        self.assertEqual(response.status_code, 201, response.data)
        return f"{base}url-registry/{response.data['id']}/", response.data['id']

    def linked_repo(self, entry):
        """The entry's repository in the registry API and in its search document (the repo and repoOwner facets)"""
        path, entry_id = entry
        response = self.client.get(path, **self.auth)
        self.assertEqual(response.status_code, 200)
        URLRegistryDocument._index.refresh()  # pylint: disable=protected-access
        hits = URLRegistryDocument.search().filter('ids', values=[str(entry_id)]).execute()
        self.assertEqual(len(hits), 1)
        return get(response.data, 'repo.url'), (get(hits[0], 'repo'), get(hits[0], 'repo_owner'))

    def test_a_repository_created_after_its_entries_links_them_and_a_canonical_change_moves_the_links(self):
        old_entries = [self.create_entry('/', self.OLD), self.create_entry(self.org.uri, self.OLD)]
        new_entry = self.create_entry(self.org.uri, self.NEW)
        for entry in old_entries + [new_entry]:
            self.assertEqual(self.linked_repo(entry), (None, (None, None)))

        with queued_tasks_run(self) as queued:
            response = self.client.post(f'{self.org.uri}sources/', {
                'id': 'codes', 'name': 'codes', 'short_code': 'codes', 'canonical_url': self.OLD,
                'default_locale': 'en', 'supported_locales': 'en'}, format='json', **self.auth)
        self.assertEqual(response.status_code, 201, response.data)
        queued.assert_called_once()
        codes = f'{self.org.uri}sources/codes/'
        for entry in old_entries:
            self.assertEqual(self.linked_repo(entry), (codes, ('codes', 'acme-registry')))
        self.assertEqual(self.linked_repo(new_entry), (None, (None, None)))

        with queued_tasks_run(self) as queued:
            response = self.client.put(codes, {'canonical_url': self.NEW}, format='json', **self.auth)
        self.assertEqual(response.status_code, 200, response.data)
        queued.assert_called_once()  # the entries of the old URL it was linked to, and the new URL's
        for entry in old_entries:
            self.assertEqual(self.linked_repo(entry), (None, (None, None)))
        self.assertEqual(self.linked_repo(new_entry), (codes, ('codes', 'acme-registry')))

    def test_a_version_given_an_entrys_canonical_links_it_and_loses_it_again(self):
        """A version row that gains the canonical an unlinked entry names is what the registry's lookup finds"""
        head_entry = self.create_entry(self.org.uri, self.OLD)
        with queued_tasks_run(self):
            head = source(self.org, 'codes', canonical_url=self.OLD)
            source(self.org, 'codes', 'v1', released=True, canonical_url=self.OLD)
        head_link = (head.uri, ('codes', 'acme-registry'))
        self.assertEqual(self.linked_repo(head_entry), head_link)  # a new version doesn't take HEAD's entry

        new_entries = [self.create_entry('/', self.NEW), self.create_entry(self.org.uri, self.NEW)]
        version_url = f'{head.uri}v1/'
        with queued_tasks_run(self):
            response = self.client.put(version_url, {'canonical_url': self.NEW}, format='json', **self.auth)
        self.assertEqual(response.status_code, 200, response.data)
        for entry in new_entries:
            self.assertEqual(self.linked_repo(entry), (version_url, ('codes', 'acme-registry')))
        self.assertEqual(self.linked_repo(head_entry), head_link)

        with queued_tasks_run(self) as queued:
            response = self.client.put(
                version_url, {'canonical_url': 'https://example.org/acme/elsewhere'}, format='json', **self.auth)
        self.assertEqual(response.status_code, 200, response.data)
        queued.assert_called_once()
        for entry in new_entries:
            self.assertEqual(self.linked_repo(entry), (None, (None, None)))
        self.assertEqual(self.linked_repo(head_entry), head_link)

    def entry_state(self, entry):
        """(exists, active) for an entry, read from the database"""
        row = URLRegistry.objects.filter(id=entry[1]).values_list('is_active', flat=True).first()
        return row is not None, bool(row)

    def create_source(self, canonical_url):
        with queued_tasks_run(self):
            response = self.client.post(f'{self.org.uri}sources/', {
                'id': 'codes', 'name': 'codes', 'short_code': 'codes', 'canonical_url': canonical_url,
                'default_locale': 'en', 'supported_locales': 'en'}, format='json', **self.auth)
        self.assertEqual(response.status_code, 201, response.data)
        return f'{self.org.uri}sources/codes/'

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_deleting_a_repository_unlinks_its_entries_instead_of_deleting_them(self, _):
        """
        A hard delete of a repository (HEAD and versions) keeps every registry entry linked to it, global and
        owner-scoped alike, unlinked; recreating the repository links them again
        """
        entries = [self.create_entry('/', self.OLD), self.create_entry(self.org.uri, self.OLD)]
        codes = self.create_source(self.OLD)
        source(self.org, 'codes', 'v1', released=True, canonical_url=self.OLD)
        for entry in entries:
            self.assertEqual(self.linked_repo(entry), (codes, ('codes', 'acme-registry')))

        response = self.client.delete(codes, **self.auth)
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Source.objects.filter(mnemonic='codes', organization=self.org).exists())
        for entry in entries:
            self.assertEqual(self.entry_state(entry), (True, True))
            self.assertEqual(self.linked_repo(entry), (None, (None, None)))

        self.assertEqual(self.create_source(self.OLD), codes)
        for entry in entries:
            self.assertEqual(self.linked_repo(entry), (codes, ('codes', 'acme-registry')))

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_deleting_a_version_relinks_its_entry_to_what_it_resolves_to_now(self, _):
        """An entry linked to a version row survives that version's delete and moves to the released sibling"""
        source(self.org, 'codes', canonical_url=self.OLD)
        source(self.org, 'codes', 'v1', released=True, canonical_url=self.NEW, is_latest_version=False)
        source(self.org, 'codes', 'v2', released=True, canonical_url=self.NEW, is_latest_version=True)
        entry = self.create_entry('/', self.NEW)
        v1_url, v2_url = f'{self.org.uri}sources/codes/v1/', f'{self.org.uri}sources/codes/v2/'
        self.assertEqual(self.linked_repo(entry), (v1_url, ('codes', 'acme-registry')))

        response = self.client.delete(v1_url, **self.auth)
        self.assertEqual(response.status_code, 204, getattr(response, 'data', None))
        self.assertEqual(self.entry_state(entry), (True, True))
        self.assertEqual(self.linked_repo(entry), (v2_url, ('codes', 'acme-registry')))

    def fail_once(self):
        """A delete that fails part-way: after the entries are unlinked and, for a HEAD, its versions deleted"""
        return patch.object(ConceptContainerModel, 'delete_version_checksum_maps', side_effect=RuntimeError('fail'))

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_a_failed_repository_delete_keeps_its_entries_and_a_retry_finishes_it(self, _):
        entries = [self.create_entry('/', self.OLD), self.create_entry(self.org.uri, self.OLD)]
        codes = self.create_source(self.OLD)
        source(self.org, 'codes', 'v1', released=True, canonical_url=self.OLD)

        with self.fail_once():
            response = self.client.delete(codes, **self.auth)
        self.assertEqual(response.status_code, 400, response.data)  # delete_source reports the failure
        for entry in entries:
            self.assertEqual(self.entry_state(entry), (True, True))

        response = self.client.delete(codes, **self.auth)
        self.assertEqual(response.status_code, 204, response.data)
        for entry in entries:
            self.assertEqual(self.entry_state(entry), (True, True))
            self.assertEqual(self.linked_repo(entry), (None, (None, None)))

        self.assertEqual(self.create_source(self.OLD), codes)
        for entry in entries:
            self.assertEqual(self.linked_repo(entry), (codes, ('codes', 'acme-registry')))

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_a_retried_repository_delete_relinks_entries_its_failed_attempt_unlinked(self, _):
        """The retry finds the entries by the deleted rows' canonical URL and links them to what is left"""
        entry = self.create_entry('/', self.OLD)
        codes = self.create_source(self.OLD)
        other = source(self.org, 'codes-2', canonical_url=self.OLD)
        self.assertEqual(self.linked_repo(entry), (codes, ('codes', 'acme-registry')))

        with self.fail_once():
            self.assertEqual(self.client.delete(codes, **self.auth).status_code, 400)
        self.assertEqual(self.entry_state(entry), (True, True))

        self.assertEqual(self.client.delete(codes, **self.auth).status_code, 204)
        self.assertEqual(URLRegistry.objects.get(id=entry[1]).repo_id, other.id)
        self.assertEqual(self.linked_repo(entry), (other.uri, ('codes-2', 'acme-registry')))

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_a_failed_version_delete_keeps_its_entry_and_a_retry_moves_it(self, _):
        source(self.org, 'codes', canonical_url=self.OLD)
        source(self.org, 'codes', 'v1', released=True, canonical_url=self.NEW, is_latest_version=False)
        v2 = source(self.org, 'codes', 'v2', released=True, canonical_url=self.NEW, is_latest_version=True)
        entry = self.create_entry('/', self.NEW)
        v1_url, v2_url = f'{self.org.uri}sources/codes/v1/', f'{self.org.uri}sources/codes/v2/'
        self.assertEqual(self.linked_repo(entry), (v1_url, ('codes', 'acme-registry')))

        with self.fail_once(), self.assertRaises(RuntimeError):
            self.client.delete(v1_url, **self.auth)
        self.assertEqual(self.entry_state(entry), (True, True))

        response = self.client.delete(v1_url, **self.auth)
        self.assertEqual(response.status_code, 204, getattr(response, 'data', None))
        self.assertEqual(self.entry_state(entry), (True, True))
        self.assertEqual(URLRegistry.objects.get(id=entry[1]).repo_id, v2.id)
        self.assertEqual(self.linked_repo(entry), (v2_url, ('codes', 'acme-registry')))

    def test_a_stale_entry_saves_only_its_link(self):
        """Saving the cached link writes only the link columns, so a stale instance can't undo a concurrent edit"""
        other = OrganizationFactory(mnemonic='acme-other')
        codes = source(self.org, 'codes', canonical_url=self.OLD)
        entry = self.create_entry('/', self.OLD)
        stale = URLRegistry.objects.get(id=entry[1])
        self.assertEqual(stale.repo, codes)

        URLRegistry.objects.filter(id=stale.id).update(namespace=other.uri, name='redirected')
        stale.unlink()
        row = URLRegistry.objects.get(id=stale.id)
        self.assertEqual(
            (row.namespace, row.name, row.repo_type_id, row.repo_id), (other.uri, 'redirected', None, None))
        self.assertEqual(self.linked_repo(entry), (None, (None, None)))

        stale.lookup_entry()  # the stale namespace still finds codes; only the link is written
        row = URLRegistry.objects.get(id=stale.id)
        self.assertEqual((row.namespace, row.name, row.repo_id), (other.uri, 'redirected', codes.id))
        self.assertEqual(self.linked_repo(entry), (codes.uri, ('codes', 'acme-registry')))

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_a_namespace_redirected_during_a_delete_is_kept_and_resolved(self, _):
        """The entry is redirected to another namespace between the delete loading it and unlinking it"""
        other = OrganizationFactory(mnemonic='acme-other')
        source(self.org, 'codes', canonical_url=self.OLD)
        other_codes = source(other, 'codes', canonical_url=self.OLD)
        entry = self.create_entry('/', self.OLD)
        unlink = URLRegistry.unlink

        def unlink_after_a_concurrent_redirect(instance):
            URLRegistry.objects.filter(id=instance.id).update(namespace=other.uri)
            unlink(instance)

        with patch.object(URLRegistry, 'unlink', autospec=True, side_effect=unlink_after_a_concurrent_redirect):
            self.assertEqual(self.client.delete(f'{self.org.uri}sources/codes/', **self.auth).status_code, 204)
        row = URLRegistry.objects.get(id=entry[1])
        self.assertEqual((row.namespace, row.repo_id), (other.uri, other_codes.id))
        self.assertEqual(self.linked_repo(entry), (other_codes.uri, ('codes', 'acme-other')))

    def test_a_collection_created_after_its_entry_links_it(self):
        entry = GlobalURLRegistryFactory(url=self.OLD, namespace=self.org.uri)
        with queued_tasks_run(self):
            source(self.org, 'codes', canonical_url=self.OLD.replace('codes', 'other'))
            value_set = collection(self.org, 'codes-vs', canonical_url=self.OLD)
        entry.refresh_from_db()
        self.assertEqual(entry.repo, value_set)

        # neither a new version row nor a later repository with the same canonical takes the entry over
        with queued_tasks_run(self):
            collection(self.org, 'codes-vs', 'v1', released=True, canonical_url=self.OLD)
            collection(self.org, 'codes-vs-2', canonical_url=self.OLD)
        entry.refresh_from_db()
        self.assertEqual(entry.repo, value_set)

    def test_entries_elsewhere_are_left_alone(self):
        other = OrganizationFactory(mnemonic='acme-other')
        entry = GlobalURLRegistryFactory(url=self.OLD, namespace=other.uri)
        inactive = GlobalURLRegistryFactory(url=self.NEW, namespace=self.org.uri, is_active=False)
        with queued_tasks_run(self) as queued:
            source(self.org, 'codes', canonical_url=self.OLD)
            source(self.org, 'codes-2', canonical_url=self.NEW)
        queued.assert_not_called()
        for unlinked in (entry, inactive):
            unlinked.refresh_from_db()
            self.assertIsNone(unlinked.repo_id)


class URLRegistryLinkCostTest(OCLTestCase):
    """
    A row given a canonical URL, new or changed, asks the registry one question in the request: whether any active entry
    is linked to it or names its URL in its namespace. The entries move in the resolve_url_registry_entries task.
    """
    URL = 'https://example.org/acme/cost'

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(mnemonic='acme-cost')

    def save(self, build):
        """(what build() returns, the registry's queries it ran, the task calls it queued) with no entry resolved"""
        with CaptureQueriesContext(connection) as context, patch.object(URLRegistry, 'lookup_entry') as lookup, \
                patch.object(resolve_url_registry_entries, 'apply_async') as queued:
            row = build()
        lookup.assert_not_called()
        return row, [query['sql'] for query in context.captured_queries if 'url_registries' in query['sql']], queued

    def test_a_row_with_no_entry_asks_once_and_queues_nothing(self):
        head, queries, queued = self.save(lambda: source(self.org, 'codes', canonical_url=self.URL))
        self.assertEqual(len(queries), 1)
        queued.assert_not_called()

        _, queries, queued = self.save(lambda: source(self.org, 'codes', 'v1', released=True, canonical_url=self.URL))
        self.assertEqual(len(queries), 1)
        queued.assert_not_called()

        head.canonical_url = f'{self.URL}-2'
        _, queries, queued = self.save(head.save)
        self.assertEqual(len(queries), 1)
        queued.assert_not_called()

    def test_a_row_an_entry_names_queues_the_task_that_links_it(self):
        entry = GlobalURLRegistryFactory(url=self.URL, namespace=self.org.uri)
        other = OrganizationFactory(mnemonic='acme-else')
        elsewhere = OrganizationURLRegistryFactory(organization=other, url=self.URL, namespace=other.uri)

        with patch.object(resolve_url_registry_entries, 'apply_async') as queued:
            with self.captureOnCommitCallbacks(execute=True):
                with patch.object(URLRegistry, 'lookup_entry') as lookup:
                    head = source(self.org, 'codes', canonical_url=self.URL)
                lookup.assert_not_called()
                queued.assert_not_called()  # until the row commits
        queued.assert_called_once_with((head.id, 'Source'), queue='default', permanent=False)

        resolve_url_registry_entries(head.id, 'Source')
        entry.refresh_from_db()
        elsewhere.refresh_from_db()
        self.assertEqual((entry.repo, elsewhere.repo), (head, None))

    def test_the_task_queued_is_the_saved_rows_whatever_the_instance_becomes(self):
        GlobalURLRegistryFactory(url=self.URL, namespace=self.org.uri)
        with patch.object(resolve_url_registry_entries, 'apply_async') as queued:
            with self.captureOnCommitCallbacks(execute=True):
                head = source(self.org, 'codes', canonical_url=self.URL)
                saved_id, head.id = head.id, None  # as an instance reused for the next version
        queued.assert_called_once_with((saved_id, 'Source'), queue='default', permanent=False)

    def test_the_task_skips_a_row_deleted_meanwhile(self):
        resolve_url_registry_entries(0, 'Source')

    @patch('core.common.models.delete_s3_objects')  # export files: no broker in tests
    def test_a_delete_resolves_only_the_entries_of_its_namespace(self, _):
        head = source(self.org, 'codes', canonical_url=self.URL)
        GlobalURLRegistryFactory(url=self.URL, namespace=OrganizationFactory(mnemonic='acme-else').uri)
        mine = OrganizationURLRegistryFactory(organization=self.org, url=self.URL, namespace=self.org.uri)

        with patch.object(URLRegistry, 'lookup_entry', autospec=True, side_effect=URLRegistry.lookup_entry) as lookup:
            head.delete(force=True)
        self.assertEqual([call.args[0].id for call in lookup.call_args_list], [mine.id])


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
        latest released version, that version's HEAD for its view access (ocl_online#486) and the serializer (2);
        ?namespace= adds a toggle read. The registry adds the global entry, its namespace owner and a Source check
        before the read; there ?namespace= also swaps the global entry and its owner for the namespace owner and its
        own entry.
        """
        for registry, query, count in ((False, '', 9), (False, '?namespace=/orgs/org-b/', 10),
                                       (True, '', 12), (True, '?namespace=/orgs/org-b/', 13)):
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
