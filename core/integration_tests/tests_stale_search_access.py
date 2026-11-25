import uuid

from django.contrib.auth.models import AnonymousUser, Group
from django.test import tag

from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory, UserCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, RETIRED_ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase, PREVIEW_GROUP_NAME
from core.common.views import BaseAPIView
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.documents import OrganizationDocument
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.documents import SourceDocument
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


@tag('es')
class StaleSearchDocumentAccessTest(OCLAPITestCase):
    """
    Rows made private whose search documents haven't been reindexed yet, so the index still has them as public: a
    search finds them, but the hits load through the database's access check.
    """
    def setUp(self):
        super().setUp()
        self.token = 'stale' + uuid.uuid4().hex[:8]
        self.staff = UserProfile.objects.get(username='ocladmin')
        self.org = OrganizationFactory(mnemonic=self.token + 'org')
        self.member = UserProfileFactory(organizations=[self.org])
        self.outsider = UserProfileFactory()
        self.creator = UserProfileFactory()  # created the rows, but isn't a member
        created = {'created_by': self.creator, 'updated_by': self.creator}
        self.source = OrganizationSourceFactory(organization=self.org, mnemonic=self.token + 'src', **created)
        self.collection = OrganizationCollectionFactory(organization=self.org, mnemonic=self.token + 'coll', **created)
        self.concept = ConceptFactory(parent=self.source, mnemonic=self.token + 'concept', **created)
        self.mapping = MappingFactory(
            parent=self.source, mnemonic=self.token + 'map', from_concept=self.concept, to_concept=self.concept,
            **created)
        for model in (Concept, Mapping):  # the factories' latest version rows too
            model.objects.filter(parent=self.source).update(**created)
        OrganizationDocument().update([self.org], refresh=True)
        SourceDocument().update([self.source], refresh=True)
        CollectionDocument().update([self.collection], refresh=True)
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=self.source), refresh=True)

    def searches(self):
        """Each search's URL, and the id its hit has."""
        token = self.token
        return [
            (f'/orgs/?q={token}org', self.org.mnemonic),
            (f'/sources/?q={token}src', self.source.mnemonic),
            (f'/collections/?q={token}coll', self.collection.mnemonic),
            (f'/repos/?q={token}src', self.source.mnemonic),
            (f'/concepts/?q={token}concept', self.concept.mnemonic),
            (f'/mappings/?q={token}map', self.mapping.mnemonic),
        ]

    def search_ids(self, url, user=None):
        kwargs = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200, url)
        return [item.get('id') or item.get('short_code') for item in response.data]

    def make_private_without_reindex(self):
        Organization.objects.filter(id=self.org.id).update(public_access=ACCESS_TYPE_NONE)
        for model in (Source, Collection):
            model.objects.filter(organization=self.org).update(public_access=ACCESS_TYPE_NONE)
        for model in (Concept, Mapping):
            model.objects.filter(parent=self.source).update(public_access=ACCESS_TYPE_NONE)

    def test_a_stale_public_document_is_no_hit_for_the_public_or_an_outsider(self):
        for url, _id in self.searches():
            self.assertIn(_id, self.search_ids(url), url)  # the index finds it

        self.make_private_without_reindex()

        for url, _id in self.searches():
            for user in (None, self.outsider):
                with self.subTest(url=url, user=user):
                    self.assertNotIn(_id, self.search_ids(url, user))
        response = self.client.get(f'/sources/?q={self.token}src')
        self.assertEqual((response['num_found'], response['num_returned']), ('1', '0'))  # num_found: the index's

    def facet_names(self, url, user=None, lower=True):
        """Each facet's names, lowercased unless `lower` is False, as `url` answers them (facetsOnly)."""
        kwargs = {'HTTP_AUTHORIZATION': 'Token ' + user.get_token()} if user else {}
        response = self.client.get(f'{url}&facetsOnly=true', **kwargs)
        self.assertEqual(response.status_code, 200, url)
        return {facet: {str(bucket[0]).lower() if lower else str(bucket[0]) for bucket in buckets}
                for facet, buckets in response.data['facets']['fields'].items()}

    def test_a_stale_document_s_repositories_are_no_facet_name_for_the_public_or_an_outsider(self):
        source, owner = self.source.mnemonic.lower(), self.org.mnemonic.lower()
        names = {'source': source, 'owner': owner, 'fromConceptSource': source, 'toConceptSource': source,
                 'fromConceptOwner': owner, 'toConceptOwner': owner}
        token = self.token
        searches = [(f'/concepts/?q={token}concept', ('source', 'owner')),
                    (f'/mappings/?q={token}map', tuple(names)),
                    (f'/sources/?q={token}src', ('owner',)), (f'/collections/?q={token}coll', ('owner',)),
                    (f'/repos/?q={token}src', ('owner',))]
        for url, facets in searches:
            for facet in facets:
                self.assertIn(names[facet], self.facet_names(url).get(facet, set()), (url, facet))  # the index's

        self.make_private_without_reindex()

        for url, facets in searches:
            for user, named in ((None, False), (self.outsider, False), (self.member, True), (self.staff, True)):
                for facet in facets:
                    with self.subTest(url=url, user=user, facet=facet):
                        self.assertEqual(names[facet] in self.facet_names(url, user).get(facet, set()), named)

    def test_a_public_case_twin_doesn_t_keep_a_concept_facet_s_private_source_or_owner(self):
        """A concept search's source and owner facets keep the names' case, so they're matched as they are."""
        twin = OrganizationFactory(mnemonic=self.org.mnemonic.upper())
        OrganizationSourceFactory(organization=twin, mnemonic=self.source.mnemonic.upper())
        self.make_private_without_reindex()

        for user in (None, self.outsider):
            facets = self.facet_names(f'/concepts/?q={self.token}concept', user, lower=False)
            self.assertNotIn(self.source.mnemonic, facets.get('source', set()), user)
            self.assertNotIn(self.org.mnemonic, facets.get('owner', set()), user)

    def test_a_url_facet_hides_a_private_repository_s_url_and_keeps_one_outside_ocl(self):
        """Such as targetRepo, the sources a concept's mappings point to."""
        urls = {self.source.uri, '/orgs/NoSuchOrg/sources/NoSuchSource/'}
        Source.objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)

        for user, hidden in ((AnonymousUser(), {self.source.uri}), (self.outsider, {self.source.uri}),
                             (self.member, set())):
            _, hidden_urls = BaseAPIView.get_repo_names_by_access(user, 'uri', urls, True, [Source])
            self.assertEqual(hidden_urls, hidden, user)

    def test_a_stale_document_names_no_source_or_owner_that_no_row_has(self):
        """Such as a source deleted, or renamed, before its concepts were reindexed."""
        Source.objects.filter(organization=self.org).update(mnemonic=self.token + 'renamed')
        Organization.objects.filter(id=self.org.id).update(mnemonic=self.token + 'renamedorg')

        facets = self.facet_names(f'/concepts/?q={self.token}concept')
        self.assertNotIn(self.source.mnemonic.lower(), facets.get('source', set()))
        self.assertNotIn(self.org.mnemonic.lower(), facets.get('owner', set()))

    def test_a_concept_or_mapping_of_a_source_made_private_is_no_hit_before_its_own_row_follows(self):
        """A source made private commits before its save signal makes its concepts and mappings private."""
        Source.objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)

        for url, _id in self.searches()[4:]:
            for user, found in ((None, False), (self.outsider, False), (self.member, True), (self.creator, False)):
                with self.subTest(url=url, user=user):
                    self.assertEqual(_id in self.search_ids(url, user), found)

    def test_members_and_staff_still_find_them_their_creator_doesn_t(self):
        self.make_private_without_reindex()

        for url, _id in self.searches():
            for user in (self.member, self.staff):
                with self.subTest(url=url, user=user):
                    self.assertIn(_id, self.search_ids(url, user))
        # access follows membership: who created the concepts, mappings and repositories doesn't count
        for url, _id in self.searches():
            with self.subTest(url=url, user=self.creator):
                self.assertNotIn(_id, self.search_ids(url, self.creator))

    def test_a_public_org_stored_with_the_retired_edit_access_is_still_a_hit(self):
        Organization.objects.filter(id=self.org.id).update(public_access=RETIRED_ACCESS_TYPE_EDIT)
        OrganizationDocument().update(Organization.objects.filter(id=self.org.id), refresh=True)

        for user in (None, self.outsider):
            self.assertIn(self.org.mnemonic, self.search_ids(f'/orgs/?q={self.token}org', user))

    def test_a_former_member_s_own_org_search_drops_a_private_org(self):
        Organization.objects.filter(id=self.org.id).update(public_access=ACCESS_TYPE_NONE)
        OrganizationDocument().update(Organization.objects.filter(id=self.org.id), refresh=True)
        url = f'/user/orgs/?q={self.token}org'
        self.assertIn(self.org.mnemonic, self.search_ids(url, self.member))

        self.org.members.through.objects.filter(organization=self.org).delete()  # the index keeps its members

        self.assertNotIn(self.org.mnemonic, self.search_ids(url, self.member))

    def test_a_concept_s_collection_versions_search_loads_collections(self):
        """Its hits are collections, though it searches as a concept view does."""
        url = f'{self.source.uri}concepts/{self.concept.mnemonic}/collection-versions/?q={self.token}coll'
        for user in (None, self.outsider, self.member):
            with self.subTest(user=user):
                self.search_ids(url, user)  # answers 200

    def test_another_user_s_org_list_drops_a_stale_document_of_an_org_not_shared(self):
        url = f'/users/{self.member.username}/orgs/sources/?q={self.token}src'
        self.assertIn(self.source.mnemonic, self.search_ids(url, self.outsider))

        self.make_private_without_reindex()

        self.assertNotIn(self.source.mnemonic, self.search_ids(url, self.outsider))

    def test_match_drops_a_concept_made_private_without_a_reindex(self):
        self.outsider.groups.add(Group.objects.get(name=PREVIEW_GROUP_NAME))

        def match_ids():
            response = self.client.post(
                '/concepts/$match/?limit=10',
                {'rows': [{'id': self.concept.mnemonic}], 'target_repo_url': self.source.uri},
                HTTP_AUTHORIZATION='Token ' + self.outsider.get_token(), format='json')
            self.assertEqual(response.status_code, 200, response.data)
            return [result['id'] for result in response.data[0]['results']]

        self.assertIn(self.concept.mnemonic, match_ids())

        Concept.objects.filter(parent=self.source).update(public_access=ACCESS_TYPE_NONE)

        self.assertNotIn(self.concept.mnemonic, match_ids())


@tag('es')
class RenamedUserSearchAccessTest(OCLAPITestCase):
    """
    The index holds a renamed user's content under their old name, as owner and creator. Someone who registers that
    name searches their own scope, or everything: the renamed user's private content is no hit.
    """
    def setUp(self):
        super().setUp()
        self.token = 'renamed' + uuid.uuid4().hex[:8]
        self.old_name = self.token + 'user'
        self.renamed = UserProfileFactory(username=self.old_name)
        created = {'created_by': self.renamed, 'updated_by': self.renamed, 'public_access': ACCESS_TYPE_NONE}
        self.source = UserSourceFactory(user=self.renamed, mnemonic=self.token + 'src', **created)
        self.collection = UserCollectionFactory(user=self.renamed, mnemonic=self.token + 'coll', **created)
        self.concept = ConceptFactory(parent=self.source, mnemonic=self.token + 'concept', **created)
        self.mapping = MappingFactory(
            parent=self.source, mnemonic=self.token + 'map', from_concept=self.concept, to_concept=self.concept,
            **created)
        for model in (Concept, Mapping):  # the factories' latest version rows too
            model.objects.filter(parent=self.source).update(**created)
        SourceDocument().update([self.source], refresh=True)
        CollectionDocument().update([self.collection], refresh=True)
        ConceptDocument().update(Concept.objects.filter(parent=self.source), refresh=True)
        MappingDocument().update(Mapping.objects.filter(parent=self.source), refresh=True)

    def searches(self, prefix):
        """Own-scope searches, then global ones, which admit private content by its creator's name."""
        token = self.token
        return [
            (f'{prefix}sources/?q={token}src', self.source.mnemonic),
            (f'{prefix}collections/?q={token}coll', self.collection.mnemonic),
            (f'{prefix}repos/?q={token}src', self.source.mnemonic),
            (f'/sources/?q={token}src', self.source.mnemonic),
            (f'/concepts/?q={token}concept', self.concept.mnemonic),
            (f'/mappings/?q={token}map', self.mapping.mnemonic),
        ]

    def search_ids(self, url, user):
        response = self.client.get(url, HTTP_AUTHORIZATION='Token ' + user.get_token())
        self.assertEqual(response.status_code, 200, url)
        return [item.get('id') or item.get('short_code') for item in response.data]

    def test_a_renamed_user_s_private_content_is_no_hit_for_whoever_takes_the_old_name(self):
        for url, _id in self.searches(f'/users/{self.old_name}/'):
            self.assertIn(_id, self.search_ids(url, self.renamed), url)

        response = self.client.put(
            f'/users/{self.old_name}/', {'username': self.token + 'new'},
            HTTP_AUTHORIZATION='Token ' + self.renamed.get_token(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.renamed.refresh_from_db()
        self.assertEqual(self.renamed.username, self.token + 'new')
        taker = UserProfileFactory(username=self.old_name)

        for prefix in (f'/users/{self.old_name}/', '/user/'):
            for url, _id in self.searches(prefix):
                with self.subTest(url=url):
                    self.assertNotIn(_id, self.search_ids(url, taker))
