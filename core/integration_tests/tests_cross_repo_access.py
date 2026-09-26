import factory
from django.db.models import F
from mock import patch

from core.bundles.models import Bundle
from core.common.constants import ACCESS_TYPE_NONE
from core.common.tests import OCLAPITestCase
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory, UserSourceFactory
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


class CrossRepoAccessBaseTest(OCLAPITestCase):
    """A private org source, an outsider with their own source, and a member of the org."""

    def setUp(self):
        super().setUp()
        self.private_org = OrganizationFactory(mnemonic='PrivOrg', public_access=ACCESS_TYPE_NONE)
        self.member = UserProfileFactory(username='privmember')
        self.private_org.members.add(self.member)
        self.private_source = OrganizationSourceFactory(
            organization=self.private_org, mnemonic='PrivSource', public_access=ACCESS_TYPE_NONE)
        self.private_concept = ConceptFactory(
            parent=self.private_source, mnemonic='secret', public_access=ACCESS_TYPE_NONE,
            names=[ConceptNameFactory.build(name='Secret name', locale='en', locale_preferred=True)])

        self.outsider = UserProfileFactory(username='outsider')
        self.outsider_token = self.outsider.get_token()
        self.outsider_source = UserSourceFactory(user=self.outsider, mnemonic='OutsiderSource')

        self.public_source = OrganizationSourceFactory(mnemonic='PublicSource')
        self.public_concept = ConceptFactory(parent=self.public_source, mnemonic='public')
        self.admin = UserProfile.objects.get(username='ocladmin')

    @staticmethod
    def concept_payload(mnemonic, **kwargs):
        return {
            'id': mnemonic,
            'concept_class': 'Diagnosis',
            'datatype': 'None',
            'names': [{'locale': 'en', 'locale_preferred': True, 'name': mnemonic, 'name_type': 'Fully Specified'}],
            **kwargs
        }


class ConceptCloneAccessTest(CrossRepoAccessBaseTest):
    def clone(self, user, target_source):
        return self.client.post(
            target_source.uri + 'concepts/$clone/',
            {'expressions': [self.private_concept.uri]},
            HTTP_AUTHORIZATION='Token ' + user.get_token(),
            format='json'
        )

    @patch('core.bundles.models.Bundle.clone')
    def test_outsider_cannot_clone_from_private_source(self, bundle_clone_mock):
        response = self.clone(self.outsider, self.outsider_source)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[self.private_concept.uri]['status'], 404)
        bundle_clone_mock.assert_not_called()

    @patch('core.bundles.models.Bundle.clone')
    def test_member_and_staff_can_clone_from_private_source(self, bundle_clone_mock):
        bundle_clone_mock.return_value = Bundle(
            root=self.private_concept, repo_version=self.private_source, params={}, verbose=False)
        member_source = UserSourceFactory(user=self.member, mnemonic='MemberSource')

        for user, target_source in [(self.member, member_source), (self.admin, self.outsider_source)]:
            response = self.clone(user, target_source)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data[self.private_concept.uri]['status'], 200)
            self.assertEqual(bundle_clone_mock.call_args[0][1], self.private_source)


class ParentConceptAccessTest(CrossRepoAccessBaseTest):
    def test_outsider_cannot_link_or_version_private_parent(self):
        private_versions = Concept.objects.filter(versioned_object_id=self.private_concept.id).count()

        response = self.client.post(
            self.outsider_source.concepts_url,
            self.concept_payload(
                'child', parent_concept_urls=[self.private_concept.uri, self.public_concept.uri]),
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        child = Concept.objects.get(mnemonic='child', id=F('versioned_object_id'))
        self.assertEqual(child.parent_concept_urls, [self.public_concept.uri])
        self.assertEqual(
            Concept.objects.filter(versioned_object_id=self.private_concept.id).count(), private_versions)

    def test_outsider_cannot_add_private_parent_on_update(self):
        child = ConceptFactory(parent=self.outsider_source, mnemonic='child2')

        response = self.client.put(
            child.uri,
            self.concept_payload('child2', concept_class='Drug', parent_concept_urls=[self.private_concept.uri]),
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        child.refresh_from_db()
        self.assertEqual(child.parent_concept_urls, [])

    def test_member_can_link_private_parent(self):
        member_child_source = OrganizationSourceFactory(
            organization=self.private_org, mnemonic='PrivChildSource', public_access=ACCESS_TYPE_NONE)

        response = self.client.post(
            member_child_source.concepts_url,
            self.concept_payload('memberchild', parent_concept_urls=[self.private_concept.uri]),
            HTTP_AUTHORIZATION='Token ' + self.member.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        child = Concept.objects.get(mnemonic='memberchild', id=F('versioned_object_id'))
        self.assertEqual(child.parent_concept_urls, [self.private_concept.uri])

    def persist_child(self, mnemonic, parent_concept_urls):
        child = Concept.persist_new({
            **factory.build(dict, FACTORY_CLASS=ConceptFactory), 'mnemonic': mnemonic, 'parent': self.public_source,
            'names': [ConceptNameFactory.build(locale='en', locale_preferred=True)],
            'parent_concept_urls': parent_concept_urls
        }, self.member)
        self.assertEqual(child.errors, {})
        self.assertEqual(sorted(child.parent_concept_urls), sorted(parent_concept_urls))
        return child

    def test_existing_private_parent_is_kept_on_update(self):
        child = self.persist_child('keptchild', [self.private_concept.uri])
        # can edit the child's source, but can't view the private parent
        self.public_source.organization.members.add(self.outsider)

        response = self.client.put(
            child.uri,
            self.concept_payload(
                'keptchild', concept_class='Drug',
                parent_concept_urls=[self.private_concept.uri, self.public_concept.uri]),
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        child.refresh_from_db()
        self.assertEqual(sorted(child.parent_concept_urls), sorted([self.private_concept.uri, self.public_concept.uri]))

    def test_include_parent_concepts_hides_private_parents(self):
        child = self.persist_child('shownchild', [self.private_concept.uri, self.public_concept.uri])

        response = self.client.get(
            child.uri + '?includeParentConcepts=true', HTTP_AUTHORIZATION='Token ' + self.outsider_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([concept['id'] for concept in response.data['parent_concepts']], ['public'])

        for user in [self.member, self.admin]:
            response = self.client.get(
                child.uri + '?includeParentConcepts=true', HTTP_AUTHORIZATION='Token ' + user.get_token())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                sorted(concept['id'] for concept in response.data['parent_concepts']), ['public', 'secret'])


class MappingTargetAccessTest(CrossRepoAccessBaseTest):
    def test_outsider_mapping_to_private_concept_is_external(self):
        from_concept = ConceptFactory(parent=self.outsider_source, mnemonic='from')

        response = self.client.post(
            self.outsider_source.mappings_url,
            {'map_type': 'SAME-AS', 'from_concept_url': from_concept.uri, 'to_concept_url': self.private_concept.uri},
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        mapping = self.outsider_source.mappings.first()
        self.assertIsNone(mapping.to_concept_id)
        self.assertIsNone(mapping.to_source_id)
        self.assertEqual(mapping.to_concept_code, 'secret')
        self.assertEqual(mapping.to_source_url, self.private_source.uri)

        response = self.client.get(
            mapping.uri + '?lookupToConcept=true&lookupToSource=true',
            HTTP_AUTHORIZATION='Token ' + self.outsider_token
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Secret name', str(response.data))

    def test_outsider_mapping_to_public_concept_is_linked(self):
        from_concept = ConceptFactory(parent=self.outsider_source, mnemonic='from')

        response = self.client.post(
            self.outsider_source.mappings_url,
            {'map_type': 'SAME-AS', 'from_concept_url': from_concept.uri, 'to_concept_url': self.public_concept.uri},
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        mapping = self.outsider_source.mappings.first()
        self.assertEqual(mapping.to_concept.versioned_object_id, self.public_concept.id)
        self.assertEqual(mapping.to_source_id, self.public_source.id)

    def test_member_mapping_to_private_concept_is_linked(self):
        member_source = OrganizationSourceFactory(
            organization=self.private_org, mnemonic='MemberMapSource', public_access=ACCESS_TYPE_NONE)
        from_concept = ConceptFactory(parent=member_source, mnemonic='from')

        response = self.client.post(
            member_source.mappings_url,
            {'map_type': 'SAME-AS', 'from_concept_url': from_concept.uri, 'to_concept_url': self.private_concept.uri},
            HTTP_AUTHORIZATION='Token ' + self.member.get_token(),
            format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        mapping = member_source.mappings.first()
        self.assertEqual(mapping.to_concept.versioned_object_id, self.private_concept.id)
        self.assertEqual(mapping.to_source_id, self.private_source.id)

    def test_edit_by_user_who_cannot_view_target_keeps_link(self):
        from_concept = ConceptFactory(parent=self.outsider_source, mnemonic='from')
        response = self.client.post(
            self.outsider_source.mappings_url,
            {'map_type': 'SAME-AS', 'from_concept_url': from_concept.uri, 'to_concept_url': self.private_concept.uri},
            HTTP_AUTHORIZATION='Token ' + self.admin.get_token(),
            format='json'
        )
        self.assertEqual(response.status_code, 201, response.data)
        mapping = self.outsider_source.mappings.filter(id=F('versioned_object_id')).first()
        linked_concept_id, linked_source_id = mapping.to_concept_id, mapping.to_source_id
        self.assertIsNotNone(linked_concept_id)

        # the edit forms re-send the target URLs with every edit
        response = self.client.put(
            mapping.uri,
            {'map_type': 'NARROWER-THAN', 'from_concept_url': from_concept.uri,
             'to_concept_url': self.private_concept.uri, 'comment': 'edit'},
            HTTP_AUTHORIZATION='Token ' + self.outsider_token,
            format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        mapping.refresh_from_db()
        self.assertEqual(mapping.to_concept_id, linked_concept_id)
        self.assertEqual(mapping.to_source_id, linked_source_id)
