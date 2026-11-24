import uuid
from datetime import timedelta

from django.test import tag

from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.common.constants import ACCESS_TYPE_NONE, ACCESS_TYPE_VIEW
from core.common.tests import OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.integration_tests.tests_access_control import AccessTestMixin, SourceVersionReadAccessMixin
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.tests.factories import UserProfileFactory


@tag('es')
class SearchMembershipAccessTest(AccessTestMixin, OCLAPITestCase):
    """
    Searches outside the requester's own scope show a private repository's sources, collections, concepts and mappings
    to its current members, its owner and staff, as lists and reads do: not to who created them once they've left.
    """
    def setUp(self):
        super().setUp()
        self.patch_concept_es_mapping_for_ci()
        self.token = 'member' + uuid.uuid4().hex[:8]
        self.former = UserProfileFactory(organizations=[self.org])  # creates everything, then leaves the org
        self.source = self.create_private_content(self.org, self.token)
        self.former.organizations.remove(self.org)
        self.own_source = UserSourceFactory(
            user=self.owner, mnemonic=self.token + 'ownsrc', public_access=ACCESS_TYPE_NONE)
        self.own_concept = ConceptFactory(
            parent=self.own_source, mnemonic=self.token + 'ownconcept', public_access=ACCESS_TYPE_NONE)
        self.index(self.own_source)

    def create_private_content(self, org, token, **kwargs):
        created = {'created_by': self.former, 'updated_by': self.former}
        source = OrganizationSourceFactory(
            organization=org, mnemonic=token + 'src', public_access=ACCESS_TYPE_NONE, **created, **kwargs)
        OrganizationCollectionFactory(
            organization=org, mnemonic=token + 'coll', public_access=ACCESS_TYPE_NONE, **created)
        concept = ConceptFactory(
            parent=source, mnemonic=token + 'concept', concept_class=token + 'class', public_access=ACCESS_TYPE_NONE,
            **created)
        MappingFactory(
            parent=source, mnemonic=token + 'map', map_type=token + 'type', from_concept=concept,
            to_concept=concept, public_access=ACCESS_TYPE_NONE, **created)
        for model in (Concept, Mapping):  # the factories' latest version rows too
            model.objects.filter(parent=source).update(**created)
        self.index(source)
        return source

    @staticmethod
    def index(source):
        SourceDocument().update([source], refresh=True)
        CollectionDocument().update(Collection.objects.filter(organization=source.organization_id), refresh=True)
        ConceptDocument().update(Concept.objects.filter(parent=source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=source), refresh=True)

    def searches(self, token=None):
        """Each global search's URL, and the id its hit has."""
        token = token or self.token
        return [(f'/sources/?q={token}src', f'{token}src'), (f'/collections/?q={token}coll', f'{token}coll'),
                (f'/concepts/?q={token}concept', f'{token}concept'), (f'/mappings/?q={token}map', f'{token}map')]

    def search(self, url, user):
        response = self.request('get', url, user)
        self.assertEqual(response.status_code, 200, url)
        return [item.get('id') or item.get('short_code') for item in response.data], response['num_found']

    def test_current_members_and_staff_find_a_private_repository_s_content_its_former_member_doesn_t(self):
        for url, _id in self.searches():
            for user, found in [(None, False), (self.outsider, False), (self.former, False), (self.member, True),
                                (self.admin, True), (self.staff, True)]:
                with self.subTest(url=url, user=user):
                    ids, num_found = self.search(url, user)
                    self.assertEqual(_id in ids, found)
                    self.assertEqual(num_found, '1' if found else '0')
                    head = self.request('head', url, user)
                    self.assertEqual(head['num_found'], '1' if found else '0')

    def test_facets_count_a_private_repository_s_content_for_current_members_only(self):
        token = self.token
        searches = [(f'/concepts/?q={token}concept', 'conceptClass', f'{token}class'),
                    (f'/mappings/?q={token}map', 'mapType', f'{token}type')]
        for url, facet, name in searches:
            for user, found in [(None, False), (self.outsider, False), (self.former, False), (self.member, True),
                                (self.staff, True)]:
                with self.subTest(url=url, user=user):
                    response = self.request('get', url + '&facetsOnly=true', user)
                    self.assertEqual(response.status_code, 200)
                    names = {str(bucket[0]).lower() for bucket in response.data['facets']['fields'].get(facet, [])}
                    self.assertEqual(name in names, found)

    def test_a_user_finds_their_own_private_content(self):
        url = f'/concepts/?q={self.token}ownconcept'
        for user, found in [(None, False), (self.outsider, False), (self.member, False), (self.owner, True),
                            (self.staff, True)]:
            with self.subTest(user=user):
                ids, _ = self.search(url, user)
                self.assertEqual(f'{self.token}ownconcept' in ids, found)

    def test_an_org_whose_name_another_org_has_in_another_case_doesn_t_match_the_other_s_content(self):
        """
        Only concepts keep their owner's name's case in the index. The others match it in any case, so they show such
        an org's private content to no one but staff, as its own scope does (get_scope_owner_criteria).
        """
        upper = OrganizationFactory(mnemonic=self.org.mnemonic.upper(), public_access=ACCESS_TYPE_VIEW)
        token = self.token + 'twin'
        self.create_private_content(upper, token)

        for url, _id in self.searches(token):
            with self.subTest(url=url):
                self.assertEqual(self.search(url, self.member), ([], '0'))
        twin_member = UserProfileFactory(organizations=[upper])
        self.assertIn(f'{token}concept', self.search(f'/concepts/?q={token}concept', twin_member)[0])
        self.assertEqual(self.search(f'/mappings/?q={token}map', twin_member), ([], '0'))
        self.assertIn(f'{token}map', self.search(f'/mappings/?q={token}map', self.staff)[0])


@tag('es')
class ReleaseLabelAccessTest(SourceVersionReadAccessMixin, OCLAPITestCase):
    """
    Under a public HEAD every revision stays visible, but the source versions named beside it (source_versions,
    latest_source_version) are only those the requester can view. Here a public v0, older than the private v1, holds
    the same revisions.
    """
    def setUp(self):
        super().setUp()
        self.public_release = OrganizationSourceFactory(
            organization=self.org, mnemonic=self.source.mnemonic, version='v0', released=True,
            public_access=ACCESS_TYPE_VIEW)
        Source.objects.filter(id=self.public_release.id).update(created_at=self.release.created_at - timedelta(days=1))
        self.add_to_version(self.mapping, self.public_release)
        self.concept.sources.add(self.public_release)
        self.released_revision.sources.add(self.public_release)
        self.patch_concept_es_mapping_for_ci()
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=self.source), refresh=True)
        self.former = UserProfileFactory(organizations=[self.org])
        self.former.organizations.remove(self.org)

    def users(self):
        """Each requester, and whether they can view the private release."""
        return [(None, False), (self.outsider, False), (self.former, False), (self.member, True), (self.staff, True)]

    def labelled(self, response):
        """{(id, version): (source_versions, latest_source_version)} of each item `response` serializes."""
        items = response.data if isinstance(response.data, list) else [response.data]
        return {(item['id'], item.get('version')): (item.get('source_versions'), item.get('latest_source_version'))
                for item in items}

    def test_revisions_name_only_the_releases_the_requester_can_view(self):
        concept, mapping = self.concept.uri, self.mapping.uri
        revision = self.released_revision
        mapping_revision = self.mapping.get_latest_version()
        for user, can_view in self.users():
            releases = [self.public_release.uri, self.release.uri] if can_view else [self.public_release.uri]
            latest = 'v1' if can_view else 'v0'
            for url, key in [(f'{concept}versions/?includeSourceVersions=true', (revision.mnemonic, revision.version)),
                             (f'{concept}{revision.version}/', (revision.mnemonic, revision.version)),
                             (f'{mapping}versions/?includeSourceVersions=true',
                              (mapping_revision.mnemonic, mapping_revision.version)),
                             (f'{mapping}{mapping_revision.version}/',
                              (mapping_revision.mnemonic, mapping_revision.version))]:
                with self.subTest(url=url, user=user):
                    response = self.request('get', url, user)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.labelled(response)[key], (releases, latest))

    def test_latest_source_version_names_the_latest_release_the_requester_can_view(self):
        for user, can_view in self.users():
            for url in [self.concept.uri, self.mapping.uri, f'{self.source.uri}concepts/?limit=100',
                        f'{self.source.uri}mappings/?limit=100', f'/mappings/?q={self.name}map']:
                with self.subTest(url=url, user=user):
                    response = self.request('get', url, user)
                    self.assertEqual(response.status_code, 200)
                    labels = {label for _, label in self.labelled(response).values()}
                    self.assertEqual('v1' in labels, can_view, labels)
                    self.assertEqual('v0' in labels, not can_view, labels)

    def test_a_revision_only_a_private_release_holds_names_no_release_for_the_public(self):
        self.released_revision.sources.remove(self.public_release)
        url = f'{self.concept.uri}{self.released_revision.version}/'
        for user, can_view in self.users():
            with self.subTest(user=user):
                response = self.request('get', url, user)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['source_versions'], [self.release.uri] if can_view else [])
                self.assertEqual(response.data['latest_source_version'], 'v1' if can_view else None)

    def test_a_collection_names_the_release_it_resolved_to_only_to_who_can_view_it(self):
        collection = OrganizationCollectionFactory(organization=self.org, public_access=ACCESS_TYPE_VIEW)
        expansion = ExpansionFactory(collection_version=collection)
        Collection.objects.filter(id=collection.id).update(expansion_uri=expansion.uri)
        expansion.concepts.add(self.released_revision)
        expansion.explicit_source_versions.add(self.release)
        for user, can_view in self.users():
            with self.subTest(user=user):
                response = self.request('get', f'{collection.uri}concepts/', user)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item['latest_source_version'] for item in response.data], ['v1' if can_view else None])
