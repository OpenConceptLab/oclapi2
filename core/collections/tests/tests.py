from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from mock import patch, Mock, PropertyMock

from core.collections.documents import CollectionDocument
from core.collections.models import CollectionReference, Collection, Expansion
from core.collections.models import ExpansionParameters
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

    def test_fix_auto_expansion(self):
        self.assertIsNone(Collection(autoexpand=False, version='v1').fix_auto_expansion())
        self.assertIsNone(Collection(autoexpand_head=False, version='HEAD').fix_auto_expansion())

        concept = ConceptFactory()
        mapping = MappingFactory()
        collection = OrganizationCollectionFactory(version='HEAD', autoexpand_head=True)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        collection_v1 = OrganizationCollectionFactory(
            organization=collection.organization, mnemonic=collection.mnemonic, version='v1', autoexpand=True)

        collection.concepts.add(concept)
        collection.mappings.add(mapping)
        collection_v1.concepts.add(concept)
        collection_v1.mappings.add(mapping)

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.mappings.count(), 1)
        self.assertTrue(collection.expansions.exists())
        self.assertFalse(collection.expansion.concepts.exists())
        self.assertFalse(collection.expansion.concepts.exists())

        self.assertEqual(collection_v1.concepts.count(), 1)
        self.assertEqual(collection_v1.mappings.count(), 1)
        self.assertFalse(collection_v1.expansions.exists())

        v1_expansion = collection_v1.fix_auto_expansion()
        self.assertIsNotNone(v1_expansion.id)
        self.assertEqual(collection_v1.expansion_uri, v1_expansion.uri)

        self.assertEqual(collection_v1.concepts.count(), 1)
        self.assertEqual(collection_v1.mappings.count(), 1)
        self.assertEqual(collection_v1.expansions.count(), 1)
        self.assertEqual(collection_v1.expansion.concepts.count(), 1)
        self.assertEqual(collection_v1.expansion.mappings.count(), 1)

        head_expansion = collection.fix_auto_expansion()
        self.assertEqual(head_expansion.id, expansion.id)
        self.assertEqual(collection.expansion_uri, head_expansion.uri)

        self.assertEqual(collection.concepts.count(), 1)
        self.assertEqual(collection.mappings.count(), 1)
        self.assertEqual(collection.expansions.count(), 1)
        self.assertEqual(head_expansion.concepts.count(), 1)
        self.assertEqual(head_expansion.mappings.count(), 1)

    @patch('core.collections.models.Collection.expansion', new_callable=PropertyMock)
    @patch('core.collections.models.Collection.batch_index')
    def test_index_children(self, batch_index_mock, expansion_mock):
        expansion_mock.return_value = Mock(concepts='concepts-qs', mappings='mappings-qs')
        collection = Collection(expansion_uri='foobar')

        collection.index_children()

        self.assertEqual(batch_index_mock.call_count, 2)
        self.assertEqual(batch_index_mock.mock_calls[0].args, ('concepts-qs', ConceptDocument))
        self.assertEqual(batch_index_mock.mock_calls[1].args, ('mappings-qs', MappingDocument))


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
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)

        seed_children_to_new_version('collection', collection_v1.id, False)  # pylint: disable=no-value-for-parameter
        collection_v1.refresh_from_db()

        self.assertEqual(collection_v1.expansions.count(), 1)
        self.assertEqual(collection_v1.references.count(), 2)
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)
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
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)

        seed_children_to_new_version('collection', collection_v1.id)  # pylint: disable=no-value-for-parameter
        collection_v1.refresh_from_db()

        self.assertEqual(collection_v1.expansions.count(), 1)
        self.assertEqual(collection_v1.references.count(), 2)
        self.assertEqual(collection_v1.concepts.count(), 0)
        self.assertEqual(collection_v1.mappings.count(), 0)
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


class ExpansionParametersTest(OCLTestCase):
    def test_apply(self):
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


