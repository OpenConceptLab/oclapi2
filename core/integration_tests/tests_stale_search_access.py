import uuid

from django.contrib.auth.models import Group
from django.test import tag

from core.collections.documents import CollectionDocument
from core.collections.models import Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.common.constants import ACCESS_TYPE_NONE, RETIRED_ACCESS_TYPE_EDIT
from core.common.tests import OCLAPITestCase, PREVIEW_GROUP_NAME
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
from core.sources.tests.factories import OrganizationSourceFactory
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

    def test_a_concept_or_mapping_of_a_source_made_private_is_no_hit_before_its_own_row_follows(self):
        """A source made private commits before its save signal makes its concepts and mappings private."""
        Source.objects.filter(id=self.source.id).update(public_access=ACCESS_TYPE_NONE)

        for url, _id in self.searches()[4:]:
            for user, found in ((None, False), (self.outsider, False), (self.member, True), (self.creator, True)):
                with self.subTest(url=url, user=user):
                    self.assertEqual(_id in self.search_ids(url, user), found)

    def test_members_staff_and_creators_still_find_them(self):
        self.make_private_without_reindex()

        for url, _id in self.searches():
            for user in (self.member, self.staff):
                with self.subTest(url=url, user=user):
                    self.assertIn(_id, self.search_ids(url, user))
        # search's access filter admits the concepts, mappings and repositories a requester created
        for url, _id in self.searches()[1:3] + self.searches()[4:]:
            with self.subTest(url=url, user=self.creator):
                self.assertIn(_id, self.search_ids(url, self.creator))

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
