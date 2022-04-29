from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from mock import patch, Mock, PropertyMock

from core.collections.documents import CollectionDocument
from core.collections.models import CollectionReference, Collection, Expansion
from core.collections.models import ExpansionParameters
from core.collections.parsers import CollectionReferenceExpressionStringToStructuredParser, \
    CollectionReferenceSourceAllExpressionToStructuredParser
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.collections.utils import is_mapping, is_concept, is_version_specified, \
    get_concept_by_expression
from core.common.constants import CUSTOM_VALIDATION_SCHEMA_OPENMRS
from core.common.tasks import add_references, seed_children_to_new_version
from core.common.tasks import update_collection_active_mappings_count
from core.common.tasks import update_collection_active_concepts_count
from core.common.tests import OCLTestCase
from core.common.utils import drop_version
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, LocalizedTextFactory
from core.mappings.documents import MappingDocument
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory


class CollectionTest(OCLTestCase):
    def test_resource_version_type(self):
        self.assertEqual(Collection().resource_version_type, 'Collection Version')

    def test_resource_type(self):
        self.assertEqual(Collection().resource_type, 'Collection')

    def test_get_search_document(self):
        self.assertEqual(Collection.get_search_document(), CollectionDocument)

    def test_collection(self):
        self.assertEqual(Collection(mnemonic='coll').collection, 'coll')
        self.assertEqual(Collection().collection, '')

    def test_is_versioned(self):
        self.assertTrue(Collection().is_versioned)

    def test_add_references(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        self.assertEqual(collection.expansion.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)
        self.assertEqual(collection.active_concepts, None)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_references([concept_expression])
        collection.refresh_from_db()

        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.uri)
        self.assertEqual(collection.expansion.concepts.first().id, concept.get_latest_version().id)
        self.assertEqual(collection.active_concepts, 1)

        _, errors = collection.add_references([concept_expression])
        self.assertEqual(
            errors, {concept_expression: ['Concept or Mapping reference name must be unique in a collection.']}
        )
        collection.refresh_from_db()
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.active_concepts, 1)

    def test_add_references_openmrs_schema(self):
        collection = OrganizationCollectionFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        self.assertEqual(collection.expansion.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_references([concept_expression])

        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.uri)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.concepts.first(), concept.get_latest_version())

        concept2 = ConceptFactory(parent=source, sources=[source])
        collection.add_references([concept2.uri])

        self.assertEqual(collection.expansion.concepts.count(), 2)
        self.assertEqual(collection.references.count(), 2)

    def test_delete_references(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection.add_references([concept1.uri, concept2.uri, mapping.uri])

        self.assertEqual(collection.expansion.concepts.count(), 2)
        self.assertEqual(collection.expansion.mappings.count(), 1)
        self.assertEqual(collection.references.count(), 3)

        collection.delete_references(
            [concept2.get_latest_version().uri, concept2.uri, mapping.get_latest_version().uri, mapping.uri])

        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.expansion.concepts.first().uri, concept1.get_latest_version().uri)
        self.assertEqual(collection.references.first().expression, concept1.uri)

    def test_seed_references(self):
        collection1 = OrganizationCollectionFactory()
        expansion1 = ExpansionFactory(collection_version=collection1)
        collection1.expansion_uri = expansion1.uri
        collection1.save()
        collection2 = OrganizationCollectionFactory(
            version='v1', mnemonic=collection1.mnemonic, organization=collection1.organization
        )
        expansion2 = ExpansionFactory(collection_version=collection2)
        collection2.expansion_uri = expansion2.uri
        collection2.save()

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
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        ch_locale = LocalizedTextFactory(locale_preferred=True, locale='ch')
        en_locale = LocalizedTextFactory(locale_preferred=True, locale='en')
        concept = ConceptFactory(names=[ch_locale, en_locale])
        reference = CollectionReference(expression=concept.uri, collection=collection)
        reference.save()

        self.assertEqual(collection.references.count(), 1)

        with self.assertRaises(ValidationError) as ex:
            collection.validate(reference)

        self.assertEqual(
            ex.exception.message_dict,
            {
                concept.uri: [
                    'Concept or Mapping reference name must be unique in a collection.'
                ]
            }
        )

    def test_validate_openmrs_schema_duplicate_locale_type(self):
        ch_locale = LocalizedTextFactory(locale_preferred=True, locale='ch')
        en_locale = LocalizedTextFactory(locale_preferred=True, locale='en')
        concept1 = ConceptFactory(names=[ch_locale, en_locale])
        collection = OrganizationCollectionFactory(custom_validation_schema=CUSTOM_VALIDATION_SCHEMA_OPENMRS)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept1)
        concept1_reference = CollectionReference(expression=concept1.uri, collection=collection)
        concept1_reference.save()

        concept2 = ConceptFactory(names=[ch_locale, en_locale])
        concept2_reference = CollectionReference(expression=concept2.uri, collection=collection)

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
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        collection.expansion.concepts.add(concept1)
        concept1_reference = CollectionReference(expression=concept1.uri, collection=collection)
        concept1_reference.save()

        en_locale1 = LocalizedTextFactory(locale='en', locale_preferred=False, name='name')
        en_locale2 = LocalizedTextFactory(locale='en', locale_preferred=True, name='name')
        concept2 = ConceptFactory(names=[en_locale1, en_locale2])
        concept2_reference = CollectionReference(expression=concept2.uri, collection=collection)

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
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        self.assertIsNone(collection.last_concept_update)
        concept = ConceptFactory()
        collection.expansion.concepts.add(concept)
        self.assertEqual(collection.last_concept_update, concept.updated_at)

    def test_last_mapping_update(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        self.assertIsNone(collection.last_mapping_update)
        mapping = MappingFactory()
        collection.expansion.mappings.add(mapping)
        self.assertEqual(collection.last_mapping_update, mapping.updated_at)

    def test_last_child_update(self):
        collection = OrganizationCollectionFactory()
        self.assertEqual(collection.last_child_update, collection.updated_at)

        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        self.assertEqual(collection.last_child_update, collection.updated_at)

        mapping = MappingFactory()
        collection.expansion.mappings.add(mapping)
        self.assertEqual(collection.last_child_update, mapping.updated_at)

        concept = ConceptFactory()
        collection.expansion.concepts.add(concept)
        self.assertEqual(collection.last_child_update, concept.updated_at)

    @patch('core.collections.models.Collection.expansion', new_callable=PropertyMock)
    @patch('core.collections.models.Collection.batch_index')
    def test_index_children(self, batch_index_mock, expansion_mock):
        expansion_mock.return_value = Mock(concepts='concepts-qs', mappings='mappings-qs')
        collection = Collection(expansion_uri='foobar')

        collection.index_children()

        self.assertEqual(batch_index_mock.call_count, 2)
        self.assertEqual(batch_index_mock.mock_calls[0].args, ('concepts-qs', ConceptDocument))
        self.assertEqual(batch_index_mock.mock_calls[1].args, ('mappings-qs', MappingDocument))

    def test_get_cascaded_mapping_uris_from_concept_expressions(self):
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping1 = MappingFactory(
            from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        mapping2 = MappingFactory(from_concept=concept1)
        mapping3 = MappingFactory(to_concept=concept2, parent=concept1.parent)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.mappings.set([mapping1, mapping2, mapping3])

        expressions = [concept1.get_latest_version().url, concept2.get_latest_version().url]

        self.assertEqual(collection.get_cascaded_mapping_uris_from_concept_expressions(expressions), [])

        collection.expansion_uri = expansion.url
        collection.save()

        self.assertEqual(
            sorted(collection.get_cascaded_mapping_uris_from_concept_expressions(expressions)),
            sorted([mapping1.url, mapping2.url])
        )


class CollectionReferenceTest(OCLTestCase):
    def test_uri(self):
        org = OrganizationFactory(mnemonic='MyOrg')
        collection = OrganizationCollectionFactory(organization=org, mnemonic='MyCollection', version='HEAD')
        reference = CollectionReference(expression='/foo/bar', collection=collection)
        reference.save()
        self.assertEqual(reference.uri, f'/orgs/MyOrg/collections/MyCollection/references/{reference.id}/')

        collection_v1 = OrganizationCollectionFactory(organization=org, mnemonic='MyCollection', version='v1')
        reference = CollectionReference(expression='/foo/bar', collection=collection_v1)
        reference.save()
        self.assertEqual(reference.uri, f'/orgs/MyOrg/collections/MyCollection/v1/references/{reference.id}/')

    def test_invalid_expression(self):
        reference = CollectionReference(expression='', collection=OrganizationCollectionFactory())

        with self.assertRaises(ValidationError) as ex:
            reference.full_clean()

        self.assertEqual(
            ex.exception.message_dict,
            {'expression': ['This field cannot be blank.']}
        )

    def test_reference_type(self):
        reference = CollectionReference(expression='')
        self.assertEqual(reference.reference_type, 'concepts')

        reference = CollectionReference(expression=None)
        self.assertEqual(reference.reference_type, 'concepts')

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/concepts/concept-mnemonic/',
            reference_type=None
        )
        reference.clean()
        self.assertEqual(reference.reference_type, 'concepts')

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/concepts/concept-mnemonic/',
            reference_type='mappings'  # if you set wrong it stays as is
        )
        reference.clean()
        self.assertEqual(reference.reference_type, 'mappings')

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/mappings/mapping-mnemonic/',
            reference_type=None
        )
        reference.clean()
        self.assertEqual(reference.reference_type, 'mappings')

        reference = CollectionReference(
            expression='/parent/parent-mnemonic/sources/source-mnemonic/mappings/mapping-mnemonic/',
            reference_type='mappings'
        )
        reference.clean()
        self.assertEqual(reference.reference_type, 'mappings')

    def test_reference_as_concept_version(self):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        expression = concept.uri

        reference = CollectionReference(expression=expression, collection=collection)
        reference.full_clean()

        self.assertEqual(len(reference._concepts), 1)  # pylint: disable=protected-access
        self.assertTrue(isinstance(reference._concepts[0], Concept))  # pylint: disable=protected-access
        self.assertEqual(reference._concepts[0].id, concept.get_latest_version().id)  # pylint: disable=protected-access

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

    @patch('core.collections.models.api_get')
    def test_fetch_uris(self, api_get_mock):
        concept = ConceptFactory()
        latest_version = concept.get_latest_version()
        api_get_mock.return_value = [
            dict(
                id=concept.mnemonic,
                url=concept.uri,
                version_url=latest_version.url
            )
        ]

        ref = CollectionReference(expression=latest_version.version_url)

        self.assertEqual(
            ref.fetch_uris(concept.created_by), [latest_version.version_url]
        )
        api_get_mock.assert_called_once_with(
            latest_version.version_url, concept.created_by
        )

    def test_fetch_concepts_via_api(self):
        concept = ConceptFactory()
        ref = CollectionReference(expression='/some/expression/?q=foobar')
        ref.fetch_uris = Mock(return_value=[concept.url])
        user_mock = Mock()

        concepts = ref.fetch_concepts(user_mock)

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first(), concept)
        ref.fetch_uris.assert_called_once_with(user_mock)

    def test_fetch_mappings_via_api(self):
        mapping = MappingFactory()
        ref = CollectionReference(expression='/some/expression/?q=foobar')
        ref.fetch_uris = Mock(return_value=[mapping.url])
        user_mock = Mock()

        mappings = ref.fetch_mappings(user_mock)

        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first(), mapping)
        ref.fetch_uris.assert_called_once_with(user_mock)

    def test_concept_filter_schema(self):
        ref = CollectionReference(expression='/concepts/', filter=None)
        ref.clean()
        ref = CollectionReference(expression='/concepts/', filter='')
        ref.clean()
        ref = CollectionReference(expression='/concepts/', filter=[])
        ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/concepts/', filter=[{}, {}])
            ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/concepts/', filter=[{'foo': 'bar'}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/concepts/', filter=[{'property': 'bar'}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(
                expression='/concepts/', filter=[{'property': 'map_type', 'value': 'foobar', 'op': '='}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(
                expression='/concepts/', filter=[{'property': 'concept_class', 'value': 'foobar', 'op': 'foobar'}])
            ref.clean()
        with self.assertRaises(ValidationError) as ex:
            ref = CollectionReference(
                expression='/concepts/', filter=[{'property': 'concept_class', 'value': 'foobar', 'op': 'not in'}])
            ref.clean()

        self.assertEqual(ex.exception.message_dict, dict(filter=['Invalid filter schema.']))

        ref = CollectionReference(
            expression='/concepts/', filter=[{'property': 'concept_class', 'value': 'foobar', 'op': '='}])
        ref.clean()
        ref = CollectionReference(
            expression='/concepts/', filter=[{'property': 'external_id', 'value': 'foobar', 'op': 'in'}])
        ref.clean()

    def test_mapping_filter_schema(self):
        ref = CollectionReference(expression='/mappings/', filter=None)
        ref.clean()
        ref = CollectionReference(expression='/mappings/', filter='')
        ref.clean()
        ref = CollectionReference(expression='/mappings/', filter=[])
        ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/mappings/', filter=[{}, {}])
            ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/mappings/', filter=[{'foo': 'bar'}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/mappings/', filter=[{'property': 'bar'}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(
                expression='/mappings/', filter=[{'property': 'concept_class', 'value': 'foobar', 'op': '='}])
            ref.clean()
        with self.assertRaises(ValidationError):
            ref = CollectionReference(
                expression='/mappings/', filter=[{'property': 'map_type', 'value': 'foobar', 'op': 'foobar'}])
            ref.clean()
        with self.assertRaises(ValidationError) as ex:
            ref = CollectionReference(
                expression='/mappings/', filter=[{'property': 'map_type', 'value': 'foobar', 'op': 'not in'}])
            ref.clean()

        self.assertEqual(ex.exception.message_dict, dict(filter=['Invalid filter schema.']))

        ref = CollectionReference(
            expression='/mappings/', filter=[{'property': 'map_type', 'value': 'foobar', 'op': '='}])
        ref.clean()
        ref = CollectionReference(
            expression='/mappings/', filter=[{'property': 'external_id', 'value': 'foobar', 'op': 'in'}])
        ref.clean()


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
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
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
                mapping1.url, mapping2.get_latest_version().version_url,
            ])
        )

    @patch('core.collections.models.Collection.index_children')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task(self, export_collection_task, index_children_mock):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        concept = ConceptFactory()
        mapping = MappingFactory()
        concept_latest_version = concept.get_latest_version()
        mapping_latest_version = mapping.get_latest_version()
        collection.add_references([concept_latest_version.version_url, mapping_latest_version.version_url])

        self.assertEqual(collection.references.count(), 2)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.mappings.count(), 1)

        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, version='v1', mnemonic=collection.mnemonic
        )
        self.assertEqual(collection_v1.expansions.count(), 0)
        self.assertEqual(collection_v1.references.count(), 0)

        seed_children_to_new_version('collection', collection_v1.id, False)  # pylint: disable=no-value-for-parameter
        collection_v1.refresh_from_db()

        self.assertEqual(collection_v1.expansions.count(), 1)
        self.assertEqual(collection_v1.references.count(), 2)
        expansion = collection_v1.expansion
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.mappings.count(), 1)
        export_collection_task.delay.assert_not_called()
        index_children_mock.assert_not_called()

    @patch('core.collections.models.Collection.index_children')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task_with_export(self, export_collection_task, index_children_mock):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        concept = ConceptFactory()
        mapping = MappingFactory()
        concept_latest_version = concept.get_latest_version()
        mapping_latest_version = mapping.get_latest_version()
        collection.add_references([concept_latest_version.version_url, mapping_latest_version.version_url])

        self.assertEqual(collection.references.count(), 2)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.mappings.count(), 1)

        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, version='v1', mnemonic=collection.mnemonic
        )

        self.assertEqual(collection_v1.expansions.count(), 0)
        self.assertEqual(collection_v1.references.count(), 0)

        seed_children_to_new_version('collection', collection_v1.id)  # pylint: disable=no-value-for-parameter
        collection_v1.refresh_from_db()

        self.assertEqual(collection_v1.expansions.count(), 1)
        self.assertEqual(collection_v1.references.count(), 2)
        expansion = collection_v1.expansions.first()
        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.mappings.count(), 1)
        export_collection_task.delay.assert_called_once_with(collection_v1.id)
        index_children_mock.assert_called_once()

    def test_update_collection_active_mappings_count(self):
        mapping1 = MappingFactory()
        mapping2 = MappingFactory(retired=True)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.url
        expansion.mappings.add(mapping1)
        expansion.mappings.add(mapping2)
        collection.save()

        self.assertEqual(collection.active_mappings, None)

        update_collection_active_mappings_count(collection.id)

        collection.refresh_from_db()
        self.assertEqual(collection.active_mappings, 1)

    def test_update_collection_active_concepts_count(self):
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(retired=True)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.add(concept1)
        expansion.concepts.add(concept2)
        collection.expansion_uri = expansion.url
        collection.save()

        self.assertEqual(collection.active_concepts, None)

        update_collection_active_concepts_count(collection.id)

        collection.refresh_from_db()
        self.assertEqual(collection.active_concepts, 1)


class ExpansionTest(OCLTestCase):
    @patch('core.collections.models.seed_children_to_expansion')
    def test_persist_without_mnemonic(self, seed_children_to_expansion_mock):
        collection = OrganizationCollectionFactory()

        expansion = Expansion.persist(index=False, collection_version=collection)

        self.assertIsNotNone(expansion.id)
        self.assertEqual(expansion.id, expansion.mnemonic)
        self.assertEqual(expansion.collection_version, collection)
        seed_children_to_expansion_mock.assert_called_once_with(expansion.id, False)

    @patch('core.collections.models.seed_children_to_expansion')
    def test_persist_with_mnemonic(self, seed_children_to_expansion_mock):
        collection = OrganizationCollectionFactory()

        expansion = Expansion.persist(index=True, mnemonic='e1', collection_version=collection)

        self.assertIsNotNone(expansion.id)
        self.assertEqual(expansion.mnemonic, 'e1')
        self.assertNotEqual(expansion.id, expansion.mnemonic)
        self.assertEqual(expansion.collection_version, collection)
        seed_children_to_expansion_mock.assert_called_once_with(expansion.id, True)

    def test_owner_url(self):
        self.assertEqual(
            Expansion(uri='/orgs/org/collections/coll/HEAD/expansions/e1/').owner_url, '/orgs/org/')
        self.assertEqual(
            Expansion(uri='/users/user/collections/coll/HEAD/expansions/e1/').owner_url, '/users/user/')
        self.assertEqual(
            Expansion(uri='/orgs/org/collections/coll/v1/expansions/e1/').owner_url, '/orgs/org/')
        self.assertEqual(
            Expansion(uri='/orgs/org/collections/coll/expansions/e1/').owner_url, '/orgs/org/')

    def test_expansion(self):
        self.assertEqual(Expansion(mnemonic='e1').expansion, 'e1')

    def test_get_url_kwarg(self):
        self.assertEqual(Expansion().get_url_kwarg(), 'expansion')

    def test_get_resource_url_kwarg(self):
        self.assertEqual(Expansion().get_resource_url_kwarg(), 'expansion')

    def test_is_default(self):
        self.assertTrue(Expansion(uri='foobar', collection_version=Collection(expansion_uri='foobar')).is_default)
        self.assertFalse(Expansion(uri='foobar', collection_version=Collection(expansion_uri='foo')).is_default)
        self.assertFalse(Expansion(uri='foobar', collection_version=Collection(expansion_uri=None)).is_default)

    def test_clean(self):
        expansion = Expansion(parameters=None)

        expansion.clean()

        self.assertIsNotNone(expansion.parameters)

    def test_delete_expressions_all(self):
        collection = OrganizationCollectionFactory()
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping = MappingFactory(
            from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.set([concept1, concept2])
        expansion.mappings.set([mapping])

        expansion.delete_expressions('*')

        self.assertEqual(expansion.concepts.count(), 0)
        self.assertEqual(expansion.mappings.count(), 0)

    def test_delete_expressions_specific(self):
        collection = OrganizationCollectionFactory()
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping1 = MappingFactory(
            from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        mapping2 = MappingFactory(
            to_concept=concept1, from_concept=concept2, parent=concept1.parent)
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.set([concept1, concept2])
        expansion.mappings.set([mapping1, mapping2])

        expansion.delete_expressions(
            [concept1.url, mapping1.url]
        )

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.mappings.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept2.id)
        self.assertEqual(expansion.mappings.first().id, mapping2.id)

    def test_is_auto_generated(self):
        self.assertFalse(Expansion().is_auto_generated)
        self.assertFalse(Expansion(mnemonic=None).is_auto_generated)
        self.assertFalse(Expansion(mnemonic='').is_auto_generated)
        self.assertFalse(Expansion(mnemonic='foobar').is_auto_generated)
        self.assertFalse(Expansion(mnemonic='autoexpand-v1').is_auto_generated)
        self.assertFalse(
            Expansion(mnemonic='autoexpand-v1', collection_version=Collection(version='v2')).is_auto_generated)
        self.assertTrue(
            Expansion(mnemonic='autoexpand-v2', collection_version=Collection(version='v2')).is_auto_generated)
        self.assertTrue(
            Expansion(mnemonic='autoexpand-HEAD', collection_version=Collection(version='HEAD')).is_auto_generated)

    def test_get_mappings_for_concept(self):
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping1 = MappingFactory(parent=concept1.parent, from_concept=concept1, to_concept=concept2)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.set([concept1, concept2])
        expansion.mappings.set([mapping1])

        mappings = expansion.get_mappings_for_concept(concept1)
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.id)

        mappings = expansion.get_mappings_for_concept(concept2)
        self.assertEqual(mappings.count(), 0)

        mappings = expansion.get_mappings_for_concept(concept=concept2, include_indirect=True)
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.id)


class ExpansionParametersTest(OCLTestCase):
    def test_apply_active_only(self):
        ConceptFactory(id=1, retired=False, mnemonic='active')
        ConceptFactory(id=2, retired=True, mnemonic='retired')
        queryset = Concept.objects.filter(id__in=[1, 2])

        result = ExpansionParameters(dict(activeOnly=True)).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters(dict(activeOnly=False)).apply(queryset)
        self.assertEqual(result.count(), 2)
        self.assertEqual(
            list(result.order_by('id').values_list('id', flat=True)),
            [1, 2]
        )

    def test_apply_text_filter(self):
        ConceptFactory(id=1, mnemonic='foobar')
        ConceptFactory(id=2, mnemonic='bar')
        queryset = Concept.objects.filter(id__in=[1, 2])
        ConceptDocument().update(queryset)  # needed for parallel test execution

        result = ExpansionParameters(dict(filter='foo tao')).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters(dict(filter='foobar')).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters(dict(filter='bar')).apply(queryset)
        self.assertEqual(result.count(), 2)
        self.assertEqual(
            list(result.order_by('id').values_list('id', flat=True)),
            [1, 2]
        )

    def test_apply_exclude_system_filter(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory(
            mnemonic='s1', version='HEAD', canonical_url='https://s1.com')
        source1_v1 = OrganizationSourceFactory(
            mnemonic='s1', version='v1', canonical_url='https://s1.com', organization=source1.organization)
        source2 = OrganizationSourceFactory(
            mnemonic='s2', version='HEAD', canonical_url='https://s2.com')
        source2_v1 = OrganizationSourceFactory(
            mnemonic='s2', version='v1', canonical_url='https://s2.com', organization=source2.organization)

        concept1 = ConceptFactory(id=1, parent=source1)
        concept2 = ConceptFactory(id=2, parent=source1)  # pylint: disable=unused-variable
        concept3 = ConceptFactory(id=3, parent=source2)
        concept4 = ConceptFactory(id=4, parent=source2)  # pylint: disable=unused-variable
        concept1.sources.set([source1, source1_v1])
        concept3.sources.set([source2, source2_v1])

        collection = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='HEAD')
        collection_v1 = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='v1', organization=collection.organization)
        expansion = ExpansionFactory(mnemonic='e1', collection_version=collection)
        expansion_v1 = ExpansionFactory(mnemonic='e2', collection_version=collection_v1)

        concept5 = ConceptFactory(id=5)
        concept6 = ConceptFactory(id=6)
        expansion_v1.concepts.add(concept5)
        expansion.concepts.add(concept5, concept6)

        queryset = Concept.objects.filter(id__in=[1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': ''}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': None}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com|HEAD'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 5)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [2, 3, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com|v1,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [2, 4, 5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com,https://s2.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 2)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [5, 6])

        result = ExpansionParameters({'exclude-system': 'https://s1.com,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [4, 5, 6])

        result = ExpansionParameters({'exclude - system': 'https://s1.com,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [4, 5, 6])

        result = ExpansionParameters(
            {'exclude-system': 'https://s1.com,https://s2.com|v1,http://c1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [4])

        result = ExpansionParameters(
            {'exclude-system': 'https://s1.com,https://s2.com,http://c1.com'}).apply(queryset)
        self.assertEqual(result.count(), 0)

        result = ExpansionParameters(
            {'exclude-system': 'http://c1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4])

        result = ExpansionParameters(
            {'exclude-system': 'http://c1.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 5)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 6])

        result = ExpansionParameters(
            {'exclude-system': 'http://c2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

    def test_apply_include_system_filter(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory(
            mnemonic='s1', version='HEAD', canonical_url='https://s1.com')
        source1_v1 = OrganizationSourceFactory(
            mnemonic='s1', version='v1', canonical_url='https://s1.com', organization=source1.organization)
        source2 = OrganizationSourceFactory(
            mnemonic='s2', version='HEAD', canonical_url='https://s2.com')
        source2_v1 = OrganizationSourceFactory(
            mnemonic='s2', version='v1', canonical_url='https://s2.com', organization=source2.organization)

        concept1 = ConceptFactory(id=1, parent=source1)
        concept2 = ConceptFactory(id=2, parent=source1)  # pylint: disable=unused-variable
        concept3 = ConceptFactory(id=3, parent=source2)
        concept4 = ConceptFactory(id=4, parent=source2)  # pylint: disable=unused-variable
        concept1.sources.set([source1, source1_v1])
        concept3.sources.set([source2, source2_v1])

        collection = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='HEAD')
        collection_v1 = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='v1', organization=collection.organization)
        expansion = ExpansionFactory(mnemonic='e1', collection_version=collection)
        expansion_v1 = ExpansionFactory(mnemonic='e2', collection_version=collection_v1)

        concept5 = ConceptFactory(id=5)
        concept6 = ConceptFactory(id=6)
        expansion_v1.concepts.add(concept5)
        expansion.concepts.add(concept5, concept6)

        queryset = Concept.objects.filter(id__in=[1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'system-version': ''}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'system-version': None}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

        result = ExpansionParameters({'system-version': 'https://s1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 2)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2])

        result = ExpansionParameters({'system-version': 'https://s1.com|HEAD'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 2)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2])

        result = ExpansionParameters({'system-version': 'https://s1.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1])

        result = ExpansionParameters({'system-version': 'https://s1.com|v1,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 2)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 3])

        result = ExpansionParameters({'system-version': 'https://s1.com,https://s2.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4])

        result = ExpansionParameters({'system-version': 'https://s1.com,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3])

        result = ExpansionParameters({'system - version': 'https://s1.com,https://s2.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3])

        result = ExpansionParameters(
            {'system-version': 'https://s1.com,https://s2.com|v1,http://c1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 5)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 5, 6])

        result = ExpansionParameters(
            {'system-version': 'https://s1.com,https://s2.com,http://c1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 6)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4, 5, 6])

        result = ExpansionParameters(
            {'system-version': 'http://c1.com'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 2)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [5, 6])

        result = ExpansionParameters(
            {'system-version': 'http://c1.com|v1'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [5])

        result = ExpansionParameters(
            {'system-version': 'http://c2.com|v1'}).apply(queryset)
        self.assertEqual(result.count(), 0)

    def test_apply_date_filter(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory(
            mnemonic='s1', version='HEAD', canonical_url='https://s1.com')
        source1_v1 = OrganizationSourceFactory(
            mnemonic='s1', version='v1', canonical_url='https://s1.com', organization=source1.organization,
            revision_date='2020-02-01'
        )
        collection = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='HEAD')
        collection_v1 = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='v1', organization=collection.organization,
            revision_date='2021-03-01 10:09:08'
        )
        expansion_v1 = ExpansionFactory(mnemonic='e2', collection_version=collection_v1)

        concept1 = ConceptFactory(id=1, parent=source1)
        concept2 = ConceptFactory(id=2, parent=source1)
        concept3 = ConceptFactory(id=3)
        concept4 = ConceptFactory(id=4)
        concept1.sources.set([source1, source1_v1])
        concept2.sources.set([source1])
        expansion_v1.concepts.add(concept1, concept3, concept4)

        queryset = Concept.objects.filter(id__in=[1, 2, 3, 4])
        result = ExpansionParameters({'date': ''}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 4)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 2, 3, 4])

        result = ExpansionParameters({'date': '2020'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1])

        result = ExpansionParameters({'date': '2021'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 3, 4])

        result = ExpansionParameters({'date': '2021-03'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 3, 4])

        result = ExpansionParameters({'date': '2020-02-01'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1])

        result = ExpansionParameters({'date': '2020,2021'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 3, 4])

        result = ExpansionParameters({'date': '2020,2022'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1])

        result = ExpansionParameters({'date': '2020-02,2021-03'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 3)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1, 3, 4])

        result = ExpansionParameters({'date': '2021-03-02'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 0)

        result = ExpansionParameters({'date': '2020-02-01 00:00:00'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 1)
        self.assertEqual(list(result.order_by('id').values_list('id', flat=True)), [1])

        result = ExpansionParameters({'date': '2020-02-01 00:00:01'}).apply(queryset)
        result = result.distinct('id')
        self.assertEqual(result.count(), 0)


class CollectionReferenceExpressionStringToStructuredParserTest(OCLTestCase):
    @staticmethod
    def get_structure(**kwargs):
        parser = CollectionReferenceExpressionStringToStructuredParser(
            **kwargs
        )
        parser.parse()
        return parser.to_reference_structure()

    def test_parse_concept_expressions(self):
        reference = self.get_structure(expression='/concepts/')
        self.assertEqual(
            reference,
            dict(
                expression='/concepts/',
                system=None,
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='concepts',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/concepts/?q=foobar&conceptClass=drug')
        self.assertEqual(
            reference,
            dict(
                expression='/concepts/?q=foobar&conceptClass=drug',
                system=None,
                valueset=None,
                filter=[
                    dict(property='q', value='foobar', op='='),
                    dict(property='conceptClass', value='drug', op='='),
                ],
                cascade=None,
                reference_type='concepts',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/concepts/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='concepts',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/concepts/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/v1/concepts/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='concepts',
                version='v1',
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/concepts/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/v1/concepts/1234/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='concepts',
                version='v1',
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/concepts/1234/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='concepts',
                version=None,
                code='1234'
            )
        )

        reference = self.get_structure(
            expression='/orgs/MyOrg/sources/MySource/concepts/1234/', cascade='sourceToConcept')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/concepts/1234/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade='sourceToConcept',
                reference_type='concepts',
                version=None,
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/concepts/?q=foo&external_id=alpha,beta',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=[
                    dict(property='q', value='foo', op='='),
                    dict(property='external_id', value='alpha,beta', op='='),
                ],
                cascade=None,
                reference_type='concepts',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/concepts/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/concepts/?q=foo&external_id=alpha,beta',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/'
                ],
                filter=[
                    dict(property='q', value='foo', op='='),
                    dict(property='external_id', value='alpha,beta', op='='),
                ],
                cascade=None,
                reference_type='concepts',
                version=None,
                code=None
            )
        )
        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                filter=None,
                cascade=None,
                reference_type='concepts',
                version=None,
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/3456/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/3456/',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                filter=None,
                cascade=None,
                reference_type='concepts',
                version=None,
                code='1234'
            )
        )

    def test_parse_mapping_expressions(self):
        reference = self.get_structure(expression='/mappings/')
        self.assertEqual(
            reference,
            dict(
                expression='/mappings/',
                system=None,
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='mappings',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/mappings/?q=foobar&mapType=Q-AND-A')
        self.assertEqual(
            reference,
            dict(
                expression='/mappings/?q=foobar&mapType=Q-AND-A',
                system=None,
                valueset=None,
                filter=[
                    dict(property='q', value='foobar', op='='),
                    dict(property='mapType', value='Q-AND-A', op='='),
                ],
                cascade=None,
                reference_type='mappings',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/mappings/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='mappings',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/mappings/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/v1/mappings/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='mappings',
                version='v1',
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/mappings/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/v1/mappings/1234/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='mappings',
                version='v1',
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/mappings/1234/',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=None,
                cascade=None,
                reference_type='mappings',
                version=None,
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/sources/MySource/mappings/?q=foo&external_id=alpha,beta',
                system='/orgs/MyOrg/sources/MySource/',
                valueset=None,
                filter=[
                    dict(property='q', value='foo', op='='),
                    dict(property='external_id', value='alpha,beta', op='='),
                ],
                cascade=None,
                reference_type='mappings',
                version=None,
                code=None
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/mappings/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/mappings/?q=foo&external_id=alpha,beta',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/'
                ],
                filter=[
                    dict(property='q', value='foo', op='='),
                    dict(property='external_id', value='alpha,beta', op='='),
                ],
                cascade=None,
                reference_type='mappings',
                version=None,
                code=None
            )
        )
        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                filter=None,
                cascade=None,
                reference_type='mappings',
                version=None,
                code='1234'
            )
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/3456/')
        self.assertEqual(
            reference,
            dict(
                expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/3456/',
                system=None,
                valueset=[
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                filter=None,
                cascade=None,
                reference_type='mappings',
                version=None,
                code='1234'
            )
        )


class CollectionReferenceSourceAllExpressionToStructuredParserTest(OCLTestCase):
    @staticmethod
    def get_structure(**kwargs):
        parser = CollectionReferenceSourceAllExpressionToStructuredParser(
            **kwargs
        )
        parser.parse()
        return parser.to_reference_structure()

    def test_parse(self):
        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/", concepts="*", mappings="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/concepts/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='concepts',
                    version=None,
                    code=None
                ),
                dict(
                    expression='/users/Me/sources/MySource/mappings/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='mappings',
                    version=None,
                    code=None
                )
            ]
        )
        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/v1/", concepts="*", mappings="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/v1/concepts/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='concepts',
                    version='v1',
                    code=None
                ),
                dict(
                    expression='/users/Me/sources/MySource/v1/mappings/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='mappings',
                    version='v1',
                    code=None
                )
            ]
        )

        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/v1/", concepts="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/v1/concepts/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='concepts',
                    version='v1',
                    code=None
                ),
            ]
        )
        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/v1/", mappings="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/v1/mappings/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='mappings',
                    version='v1',
                    code=None
                ),
            ]
        )
        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/", concepts="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/concepts/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='concepts',
                    version=None,
                    code=None
                ),
            ]
        )
        reference = self.get_structure(
            expression=dict(uri="/users/Me/sources/MySource/", mappings="*"))
        self.assertEqual(
            reference,
            [
                dict(
                    expression='/users/Me/sources/MySource/mappings/',
                    system='/users/Me/sources/MySource/',
                    valueset=None,
                    filter=None,
                    cascade=None,
                    reference_type='mappings',
                    version=None,
                    code=None
                ),
            ]
        )
