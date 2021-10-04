from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from mock import patch

from core.collections.models import CollectionReference, Collection
from core.collections.tests.factories import OrganizationCollectionFactory
from core.collections.utils import is_mapping, is_concept, is_version_specified, \
    get_concept_by_expression
from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tasks import add_references, seed_children
from core.common.tests import OCLTestCase
from core.common.utils import drop_version
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.tests.factories import MappingFactory
from core.sources.tests.factories import OrganizationSourceFactory


class CollectionTest(OCLTestCase):
    def test_resource_version_type(self):
        self.assertEqual(Collection().resource_version_type, 'Collection Version')

    def test_resource_type(self):
        self.assertEqual(Collection().resource_type, 'Collection')

    def test_collection(self):
        self.assertEqual(Collection(mnemonic='coll').collection, 'coll')
        self.assertEqual(Collection().collection, '')

    def test_is_versioned(self):
        self.assertTrue(Collection().is_versioned)

    def test_add_references(self):
        collection = OrganizationCollectionFactory()

        self.assertEqual(collection.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)
        self.assertEqual(collection.active_concepts, 0)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_references([concept_expression])
        collection.refresh_from_db()

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.get_latest_version().uri)
        self.assertEqual(collection.concepts.first(), concept.get_latest_version())
        self.assertEqual(collection.active_concepts, 1)

        result = collection.add_references([concept_expression])
        self.assertEqual(
            result, {concept_expression: ['Concept or Mapping reference name must be unique in a collection.']}
        )
        collection.refresh_from_db()
        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.active_concepts, 1)

    def test_add_references_openmrs_schema(self):
        collection = OrganizationCollectionFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)

        self.assertEqual(collection.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_references([concept_expression])

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.get_latest_version().uri)
        self.assertEqual(collection.concepts.first(), concept.get_latest_version())

        concept2 = ConceptFactory(parent=source, sources=[source])
        collection.add_references([concept2.uri])

        self.assertEqual(collection.concepts.count(), 2)
        self.assertEqual(collection.references.count(), 2)

    def test_delete_references(self):
        collection = OrganizationCollectionFactory()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection.add_references([concept1.uri, concept2.uri, mapping.uri])

        self.assertEqual(collection.concepts.count(), 2)
        self.assertEqual(collection.mappings.count(), 1)
        self.assertEqual(collection.references.count(), 3)

        collection.delete_references([concept2.get_latest_version().uri, mapping.get_latest_version().uri])

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.concepts.first().uri, concept1.get_latest_version().uri)
        self.assertEqual(collection.references.first().expression, concept1.get_latest_version().uri)

    def test_get_concepts(self):
        collection = OrganizationCollectionFactory()
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri
        collection.add_references([concept_expression])

        concepts = collection.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first(), concept.get_latest_version())
        self.assertEqual(collection.get_concepts(start=0, end=10).count(), 1)
        self.assertEqual(collection.get_concepts(start=1, end=2).count(), 0)

    def test_seed_concepts(self):
        collection1 = OrganizationCollectionFactory()
        collection2 = OrganizationCollectionFactory(
            version='v1', mnemonic=collection1.mnemonic, organization=collection1.organization
        )

        self.assertTrue(collection1.is_head)
        self.assertFalse(collection2.is_head)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection1.add_references([concept_expression])

        self.assertEqual(collection1.concepts.count(), 1)
        self.assertEqual(collection2.concepts.count(), 0)

        collection2.seed_concepts()

        self.assertEqual(collection1.concepts.count(), 1)
        self.assertEqual(collection2.concepts.count(), 1)

    def test_seed_references(self):
        collection1 = OrganizationCollectionFactory()
        collection2 = OrganizationCollectionFactory(
            version='v1', mnemonic=collection1.mnemonic, organization=collection1.organization
        )

        self.assertTrue(collection1.is_head)
        self.assertFalse(collection2.is_head)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection1.add_references([concept_expression])

        self.assertEqual(collection1.references.count(), 1)
        self.assertEqual(collection2.references.count(), 0)

        collection2.seed_references()

        self.assertEqual(collection1.references.count(), 1)
        self.assertEqual(collection2.references.count(), 1)
        self.assertEqual(collection1.references.first().expression, collection2.references.first().expression)
        self.assertNotEqual(collection1.references.first().id, collection2.references.first().id)

    def test_validate_reference_already_exists(self):
        collection = OrganizationCollectionFactory()
        ch_locale = LocalizedTextFactory(locale_preferred=True, locale='ch')
        en_locale = LocalizedTextFactory(locale_preferred=True, locale='en')
        concept = ConceptFactory(names=[ch_locale, en_locale])
        reference = CollectionReference(expression=concept.uri)
        reference.save()

        collection.references.add(reference)
        self.assertEqual(collection.references.count(), 1)

        with self.assertRaises(ValidationError) as ex:
            collection.validate(reference)

        self.assertEqual(
            ex.exception.message_dict,
            {
                concept.get_latest_version().uri: [
                    'Concept or Mapping reference name must be unique in a collection.'
                ]
            }
        )

    def test_validate_openmrs_schema_duplicate_locale_type(self):
        ch_locale = LocalizedTextFactory(locale_preferred=True, locale='ch')
        en_locale = LocalizedTextFactory(locale_preferred=True, locale='en')
        concept1 = ConceptFactory(names=[ch_locale, en_locale])
        collection = OrganizationCollectionFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        collection.concepts.add(concept1)
        concept1_reference = CollectionReference(expression=concept1.uri)
        concept1_reference.save()
        collection.references.add(concept1_reference)

        concept2 = ConceptFactory(names=[ch_locale, en_locale])
        concept2_reference = CollectionReference(expression=concept2.uri)

        with self.assertRaises(ValidationError) as ex:
            collection.validate(concept2_reference)

        self.assertEqual(
            ex.exception.message_dict,
            {'names': ['Concept fully specified name must be unique for same collection and locale.']}
        )

    def test_validate_openmrs_schema_matching_name_locale(self):
        ch_locale = LocalizedTextFactory(locale_preferred=False, locale='ch')
        concept1 = ConceptFactory(names=[ch_locale])
        collection = OrganizationCollectionFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        collection.concepts.add(concept1)
        concept1_reference = CollectionReference(expression=concept1.uri)
        concept1_reference.save()
        collection.references.add(concept1_reference)

        en_locale1 = LocalizedTextFactory(locale='en', locale_preferred=False, name='name')
        en_locale2 = LocalizedTextFactory(locale='en', locale_preferred=True, name='name')
        concept2 = ConceptFactory(names=[en_locale1, en_locale2])
        concept2_reference = CollectionReference(expression=concept2.uri)

        with self.assertRaises(ValidationError) as ex:
            collection.validate(concept2_reference)

        self.assertEqual(
            ex.exception.message_dict,
            {'names': ['Concept fully specified name must be unique for same collection and locale.']}
        )

    def test_parent_id(self):
        self.assertIsNone(Collection().parent_id)
        self.assertEqual(Collection(user_id=1).parent_id, 1)
        self.assertEqual(Collection(organization_id=1).parent_id, 1)

    def test_last_concept_update(self):
        collection = OrganizationCollectionFactory()
        self.assertIsNone(collection.last_concept_update)
        concept = ConceptFactory()
        collection.concepts.add(concept)
        self.assertEqual(collection.last_concept_update, concept.updated_at)

    def test_last_mapping_update(self):
        collection = OrganizationCollectionFactory()
        self.assertIsNone(collection.last_mapping_update)
        mapping = MappingFactory()
        collection.mappings.add(mapping)
        self.assertEqual(collection.last_mapping_update, mapping.updated_at)

    def test_last_child_update(self):
        collection = OrganizationCollectionFactory()
        self.assertEqual(collection.last_child_update, collection.updated_at)

        mapping = MappingFactory()
        collection.mappings.add(mapping)
        self.assertEqual(collection.last_child_update, mapping.updated_at)

        concept = ConceptFactory()
        collection.concepts.add(concept)
        self.assertEqual(collection.last_child_update, concept.updated_at)


class CollectionReferenceTest(OCLTestCase):
    def test_invalid_expression(self):
        reference = CollectionReference(expression='')

        with self.assertRaises(ValidationError) as ex:
            reference.full_clean()

        self.assertEqual(
            ex.exception.message_dict,
            {'expression': ['This field cannot be blank.']}
        )

    def test_reference_type(self):
        reference = CollectionReference(expression='')
        self.assertFalse(reference.reference_type)

        reference = CollectionReference(expression=None)
        self.assertFalse(reference.reference_type)

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/concepts/concept-mnemonic/'
        )
        self.assertEqual(reference.reference_type, 'concepts')

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/mappings/mapping-mnemonic/'
        )
        self.assertEqual(reference.reference_type, 'mappings')

    def test_reference_as_concept_version(self):
        concept = ConceptFactory()
        expression = concept.uri

        reference = CollectionReference(expression=expression)
        reference.full_clean()

        self.assertEqual(len(reference.concepts), 1)
        self.assertTrue(isinstance(reference.concepts[0], Concept))
        self.assertEqual(reference.concepts[0].id, concept.get_latest_version().id)

    def test_get_concepts(self):
        reference = CollectionReference()
        reference.expression = '/unknown/uri/'

        unknown_expression_concepts = reference.get_concepts()

        self.assertTrue(isinstance(unknown_expression_concepts, QuerySet))
        self.assertFalse(unknown_expression_concepts.exists())

        concept = ConceptFactory()
        reference.expression = concept.uri

        concepts = reference.get_concepts()

        self.assertTrue(isinstance(concepts, QuerySet))
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first(), concept.get_latest_version())

        ConceptFactory(parent=concept.parent, version='v1', mnemonic=concept.mnemonic, versioned_object=concept)
        reference.expression = drop_version(concept.uri) + 'versions/'

        concepts = reference.get_concepts().order_by('created_at')

        self.assertTrue(isinstance(concepts, QuerySet))
        self.assertEqual(concepts.count(), 2)
        self.assertListEqual(list(concepts.all()), list(concept.versions.all()))


class CollectionUtilsTest(OCLTestCase):
    def test_is_mapping(self):
        self.assertFalse(is_mapping(None))
        self.assertFalse(is_mapping(''))
        self.assertFalse(is_mapping('orgs/org-1/sources/source-1/mapping/'))

        self.assertTrue(is_mapping('orgs/org-1/sources/source-1/mappings/'))
        self.assertTrue(is_mapping('users/user-1/sources/source-1/mappings/'))
        self.assertTrue(is_mapping('users/user-1/collections/coll-1/mappings/'))
        self.assertTrue(is_mapping('/mappings/'))

    def test_is_concept(self):
        self.assertFalse(is_concept(None))
        self.assertFalse(is_concept(''))
        self.assertFalse(is_concept('orgs/org-1/sources/source-1/concept/'))

        self.assertTrue(is_concept('orgs/org-1/sources/source-1/concepts/'))
        self.assertTrue(is_concept('users/user-1/sources/source-1/concepts/'))
        self.assertTrue(is_concept('users/user-1/collections/coll-1/concepts/'))
        self.assertTrue(is_concept('/concepts/'))

    def test_is_version_specified(self):
        concept_head = ConceptFactory()
        concept_v1 = ConceptFactory(
            parent=concept_head.parent, version='v1', mnemonic=concept_head.mnemonic, versioned_object=concept_head
        )

        self.assertTrue(is_version_specified(concept_v1.uri))
        self.assertFalse(is_version_specified(concept_head.uri))

    def test_get_concept_by_expression(self):
        concept_head = ConceptFactory()
        concept_v1 = ConceptFactory(
            parent=concept_head.parent, version='v1', mnemonic=concept_head.mnemonic, versioned_object=concept_head
        )

        self.assertEqual(get_concept_by_expression(concept_head.uri), concept_head)
        self.assertEqual(get_concept_by_expression(concept_v1.uri), concept_v1)
        self.assertIsNone(get_concept_by_expression('/foobar/'))


class TasksTest(OCLTestCase):
    def test_add_references_task(self):
        collection = OrganizationCollectionFactory()
        concept1 = ConceptFactory()
        concept2 = ConceptFactory()
        mapping1 = MappingFactory(
            parent=concept2.parent,
            from_concept=concept2.get_latest_version(),
            to_concept=concept1.get_latest_version()
        )
        mapping2 = MappingFactory()

        errors = add_references(
            collection.created_by.id,
            dict(expressions=[
                obj.get_latest_version().version_url for obj in [concept1, concept2, mapping2]
            ]),
            collection.id,
            True
        )

        self.assertEqual(errors, {})
        self.assertListEqual(
            sorted(list(
                collection.references.values_list('expression', flat=True)
            )),
            sorted([
                concept1.get_latest_version().version_url, concept2.get_latest_version().version_url,
                mapping1.get_latest_version().version_url, mapping2.get_latest_version().version_url,
            ])
        )

    @patch('core.common.models.ConceptContainerModel.index_children')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task(self, export_collection_task, index_children_mock):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        mapping = MappingFactory()
        concept_latest_version = concept.get_latest_version()
        mapping_latest_version = mapping.get_latest_version()
        collection.add_references([concept_latest_version.version_url, mapping_latest_version.version_url])

        self.assertEqual(collection.references.count(), 2)
        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.mappings.count(), 1)

        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, version='v1', mnemonic=collection.mnemonic
        )
        self.assertEqual(collection_v1.references.count(), 0)
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)

        seed_children('collection', collection_v1.id, False)  # pylint: disable=no-value-for-parameter

        self.assertEqual(collection_v1.references.count(), 2)
        self.assertEqual(collection_v1.concepts.count(), 1)
        self.assertEqual(collection_v1.mappings.count(), 1)
        self.assertEqual(
            list(collection_v1.references.values_list('expression', flat=True)),
            list(collection.references.values_list('expression', flat=True)),
        )
        export_collection_task.delay.assert_not_called()
        index_children_mock.assert_not_called()

    @patch('core.common.models.ConceptContainerModel.index_children')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task_with_export(self, export_collection_task, index_children_mock):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        mapping = MappingFactory()
        concept_latest_version = concept.get_latest_version()
        mapping_latest_version = mapping.get_latest_version()
        collection.add_references([concept_latest_version.version_url, mapping_latest_version.version_url])

        self.assertEqual(collection.references.count(), 2)
        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.mappings.count(), 1)

        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, version='v1', mnemonic=collection.mnemonic
        )
        self.assertEqual(collection_v1.references.count(), 0)
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)

        seed_children('collection', collection_v1.id)  # pylint: disable=no-value-for-parameter

        self.assertEqual(collection_v1.references.count(), 2)
        self.assertEqual(collection_v1.concepts.count(), 1)
        self.assertEqual(collection_v1.mappings.count(), 1)
        self.assertEqual(
            list(collection_v1.references.values_list('expression', flat=True)),
            list(collection.references.values_list('expression', flat=True)),
        )
        export_collection_task.delay.assert_called_once_with(collection_v1.id)
        index_children_mock.assert_called_once()
