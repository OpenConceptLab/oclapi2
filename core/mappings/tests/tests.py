import factory
from django.core.exceptions import ValidationError

from core.common.constants import HEAD, CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tests import OCLTestCase
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.models import Mapping
from core.mappings.serializers import MappingMinimalSerializer, MappingVersionDetailSerializer, \
    MappingDetailSerializer, \
    MappingVersionListSerializer, MappingListSerializer
from core.mappings.tests.factories import MappingFactory
from core.orgs.models import Organization
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


class MappingTest(OCLTestCase):
    def test_mapping(self):
        self.assertEqual(Mapping(mnemonic='foobar').mapping, 'foobar')

    def test_source(self):
        self.assertIsNone(Mapping().source)
        self.assertEqual(Mapping(parent=Source(mnemonic='source')).source, 'source')

    def test_parent_source(self):
        source = Source(mnemonic='source')
        self.assertEqual(Mapping(parent=source).parent_source, source)

    def test_from_source_owner_mnemonic(self):
        from_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(from_concept=from_concept, from_source=from_concept.parent)

        self.assertEqual(mapping.from_source_owner_mnemonic, 'org-foo')

    def test_to_source_owner_mnemonic(self):
        to_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_source_owner_mnemonic, 'org-foo')

    def test_from_source_shorthand(self):
        from_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(from_concept=from_concept)

        self.assertEqual(mapping.from_source_shorthand, 'org-foo:foobar')

    def test_to_source_shorthand(self):
        to_concept = ConceptFactory(
            parent=OrganizationSourceFactory(mnemonic='foobar', organization=OrganizationFactory(mnemonic='org-foo'))
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_source_shorthand, 'org-foo:foobar')

    def test_from_concept_shorthand(self):
        from_concept = ConceptFactory(
            mnemonic='concept-foo',
            parent=OrganizationSourceFactory(
                mnemonic='source-foo', organization=OrganizationFactory(mnemonic='org-foo')
            )
        )
        mapping = Mapping(from_concept=from_concept, from_concept_code='concept-foo', from_source=from_concept.parent)

        self.assertEqual(mapping.from_concept_shorthand, 'org-foo:source-foo:concept-foo')

    def test_to_concept_shorthand(self):
        to_concept = ConceptFactory(
            mnemonic='concept-foo',
            parent=OrganizationSourceFactory(
                mnemonic='source-foo', organization=OrganizationFactory(mnemonic='org-foo')
            )
        )
        mapping = Mapping(to_concept=to_concept)

        self.assertEqual(mapping.to_concept_shorthand, 'org-foo:source-foo:concept-foo')

    def test_get_to_source(self):
        mapping = Mapping()

        self.assertIsNone(mapping.get_to_source())

        source = Source(id=123)
        mapping = Mapping(to_source=source)

        self.assertEqual(mapping.get_to_source(), source)

        concept = ConceptFactory()
        mapping = Mapping(to_concept=concept)

        self.assertEqual(mapping.get_to_source(), concept.parent)

    def test_get_to_concept_name(self):
        mapping = Mapping()

        self.assertIsNone(mapping.get_to_concept_name())

        mapping = Mapping(to_concept_name='to-concept-name')

        self.assertEqual(mapping.get_to_concept_name(), 'to-concept-name')

        concept = ConceptFactory(names=[LocalizedTextFactory()])
        self.assertIsNotNone(concept.display_name)

        mapping = Mapping(to_concept=concept)

        self.assertEqual(mapping.get_to_concept_name(), concept.display_name)

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
            f'/orgs/{source.organization.mnemonic}/sources/{source.mnemonic}/mappings/{mapping.mnemonic}/'
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
            f'/orgs/{source_version0.organization.mnemonic}/sources/{source_version0.mnemonic}/'
            f'{source_version0.version}/mappings/{persisted_mapping.mnemonic}/{persisted_mapping.version}/'
        )
        self.assertEqual(
            persisted_mapping.version_url, persisted_mapping.uri
        )

    def test_get_serializer_class(self):
        self.assertEqual(Mapping.get_serializer_class(), MappingListSerializer)
        self.assertEqual(Mapping.get_serializer_class(version=True), MappingVersionListSerializer)
        self.assertEqual(Mapping.get_serializer_class(verbose=True), MappingDetailSerializer)
        self.assertEqual(Mapping.get_serializer_class(verbose=True, version=True), MappingVersionDetailSerializer)
        self.assertEqual(Mapping.get_serializer_class(brief=True), MappingMinimalSerializer)


class OpenMRSMappingValidatorTest(OCLTestCase):
    def setUp(self):
        self.create_lookup_concept_classes()

    def test_single_mapping_between_concepts(self):
        source = OrganizationSourceFactory(version=HEAD, custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        mapping1 = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2)
        mapping1.populate_fields_from_relations({})
        mapping1.save()

        self.assertIsNotNone(mapping1.id)

        mapping2 = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, mnemonic='m2')
        mapping2.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping2.clean()

        self.assertEqual(ex.exception.messages, ['There can be only one mapping between two concepts'])

        mapping3 = MappingFactory.build(parent=source, to_concept=concept2, from_concept=concept1)
        mapping3.populate_fields_from_relations({})
        mapping3.clean()

    def test_invalid_map_type(self):
        source = OrganizationSourceFactory(version=HEAD, custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])

        mapping = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, map_type='Foo bar')
        mapping.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping.clean()
        self.assertEqual(ex.exception.messages, ['Invalid mapping type'])

        # 'Q-AND-A' is present in OpenMRS lookup values
        mapping = MappingFactory.build(parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A')
        mapping.populate_fields_from_relations({})
        mapping.clean()

    def test_external_id(self):
        source = OrganizationSourceFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        concept1 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])
        concept2 = ConceptFactory(parent=source, names=[LocalizedTextFactory()])

        mapping = MappingFactory.build(
            parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A', external_id='1'*37)
        mapping.populate_fields_from_relations({})

        with self.assertRaises(ValidationError) as ex:
            mapping.clean()
        self.assertEqual(ex.exception.messages, ['Mapping External ID cannot be more than 36 characters.'])

        mapping = MappingFactory.build(
            parent=source, to_concept=concept1, from_concept=concept2, map_type='Q-AND-A', external_id='1'*36)
        mapping.populate_fields_from_relations({})
        mapping.clean()
