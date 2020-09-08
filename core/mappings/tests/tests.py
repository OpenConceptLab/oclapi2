import factory

from core.common.constants import HEAD
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


class MappingTest(OCLTestCase):
    def test_source(self):
        self.assertIsNone(Mapping().source)
        self.assertEqual(Mapping(parent=Source(mnemonic='source')).source, 'source')

    def test_owner(self):
        org = Organization(id=123)
        user = UserProfile(id=123)

        self.assertIsNone(Mapping().owner)
        self.assertEqual(Mapping(parent=Source(organization=org)).owner, org)
        self.assertEqual(Mapping(parent=Source(user=user)).owner, user)

    def test_owner_name(self):
        org = Organization(id=123, mnemonic='org')
        user = UserProfile(id=123, username='user')

        self.assertEqual(Mapping().owner_name, '')
        self.assertEqual(Mapping(parent=Source(organization=org)).owner_name, 'org')
        self.assertEqual(Mapping(parent=Source(user=user)).owner_name, 'user')

    def test_owner_type(self):
        org = Organization(id=123, mnemonic='org')
        user = UserProfile(id=123, username='user')

        self.assertIsNone(Mapping().owner_type)
        self.assertEqual(Mapping(parent=Source(organization=org)).owner_type, 'Organization')
        self.assertEqual(Mapping(parent=Source(user=user)).owner_type, 'User')

    def test_persist_new(self):
        source = OrganizationSourceFactory(version=HEAD)
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = Mapping.persist_new({
            **factory.build(dict, FACTORY_CLASS=MappingFactory), 'from_concept': concept1, 'to_concept': concept2,
            'parent_id': source.id
        }, source.created_by)

        self.assertEqual(mapping.errors, {})
        self.assertIsNotNone(mapping.id)
        self.assertEqual(mapping.version, str(mapping.id))
        self.assertEqual(source.mappings_set.count(), 2)
        self.assertEqual(source.mappings.count(), 2)
        self.assertEqual(
            mapping.uri,
            '/orgs/{}/sources/{}/mappings/{}/'.format(
                source.organization.mnemonic, source.mnemonic, mapping.mnemonic
            )
        )

    def test_persist_clone(self):
        source_head = OrganizationSourceFactory(version=HEAD)
        source_version0 = OrganizationSourceFactory(
            version='v0', mnemonic=source_head.mnemonic, organization=source_head.organization
        )

        self.assertEqual(source_head.versions.count(), 2)

        mapping = MappingFactory(parent=source_version0)
        cloned_mapping = mapping.clone(mapping.created_by)

        self.assertEqual(
            Mapping.persist_clone(cloned_mapping),
            dict(version_created_by='Must specify which user is attempting to create a new mapping version.')
        )

        self.assertEqual(Mapping.persist_clone(cloned_mapping, mapping.created_by), {})

        persisted_mapping = Mapping.objects.filter(
            id=cloned_mapping.id, version=cloned_mapping.version
        ).first()
        self.assertEqual(mapping.versions.count(), 2)
        self.assertNotEqual(mapping.id, persisted_mapping.id)
        self.assertEqual(persisted_mapping.from_concept_id, mapping.from_concept_id)
        self.assertEqual(persisted_mapping.to_concept_id, mapping.to_concept_id)
        self.assertEqual(persisted_mapping.parent, source_version0)
        self.assertEqual(persisted_mapping.sources.count(), 2)
        self.assertEqual(source_head.mappings.first().id, persisted_mapping.id)
        self.assertEqual(
            persisted_mapping.uri,
            '/orgs/{}/sources/{}/{}/mappings/{}/{}/'.format(
                source_version0.organization.mnemonic, source_version0.mnemonic, source_version0.version,
                persisted_mapping.mnemonic, persisted_mapping.version
            )
        )
        self.assertEqual(
            persisted_mapping.version_url, persisted_mapping.uri
        )
