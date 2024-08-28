from django.core.exceptions import ValidationError
from mock import patch, Mock, PropertyMock
from mock.mock import ANY

from core.collections.documents import CollectionDocument
from core.collections.models import CollectionReference, Collection, Expansion
from core.collections.models import ExpansionParameters
from core.collections.parsers import CollectionReferenceExpressionStringParser, \
    CollectionReferenceSourceAllExpressionParser, CollectionReferenceOldStyleToExpandedStructureParser, \
    CollectionReferenceParser
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory, UserCollectionFactory
from core.collections.utils import is_mapping, is_concept, is_version_specified, \
    get_concept_by_expression
from core.common.constants import OPENMRS_VALIDATION_SCHEMA
from core.common.tasks import add_references, seed_children_to_new_version
from core.common.tasks import update_collection_active_concepts_count
from core.common.tasks import update_collection_active_mappings_count
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.documents import MappingDocument
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.tests.factories import OrganizationSourceFactory
from core.users.models import UserProfile


class CollectionTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        self.maxDiff = None

    def test_canonical_url_field(self):
        collection = OrganizationCollectionFactory.build()
        for uri in [
            'https://coll.com', 'http://coll.com', 'ws:coll.com', 'mailto:foo@bar.com', 'tox:skyzohkey@ricin.im',
            'tox:DFB4958A86122ACF81BB852DBC767DB8A3A7281A8EDBC83121B30C294E295869121B298FEEA2',
            'urn:oid:2.16.840.1.113883.6.238', 'telnet://192.0.2.16:80/', 'localhost:9000',
            'news:comp.infosystems.www.servers.unix', 'ldap://[2001:db8::7]/c=GB?objectClass?on',
            'ftp://ftp.is.co.za/rfc/rfc1808.txt', 'urn:oasis:names:specification:docbook:dtd:xml:4.1.2',
            '123.432.12.19:9000'
        ]:
            collection.canonical_url = uri
            collection.full_clean()

        for uri in [
            'foobar', 'foobar.com', '123.432.12.19'
        ]:
            collection.canonical_url = uri
            with self.assertRaises(ValidationError) as ex:
                collection.full_clean()
            self.assertEqual(ex.exception.message_dict, {'canonical_url': ['Enter a valid URI.']})

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

    def test_add_expressions(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        self.assertEqual(collection.expansion.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)
        self.assertEqual(collection.active_concepts, None)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        collection.add_expressions({'expressions': [concept.uri]}, collection.created_by)
        collection.refresh_from_db()

        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.uri)
        self.assertEqual(collection.expansion.concepts.first().id, concept.id)
        self.assertEqual(collection.active_concepts, 1)

        _, errors = collection.add_expressions({'concepts': [concept.uri]}, collection.created_by)
        self.assertEqual(
            errors, {
                concept.uri: {
                    concept.uri: {
                        'errors': [{
                            'description': 'Concept or Mapping reference name must be unique in a collection.',
                            'conflicting_references': [collection.references.first().uri]
                        }]
                    }
                }
            }
        )
        collection.refresh_from_db()
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.active_concepts, 1)

    def test_add_expressions_openmrs_schema(self):
        collection = OrganizationCollectionFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        self.assertEqual(collection.expansion.concepts.count(), 0)
        self.assertEqual(collection.references.count(), 0)

        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])
        concept_expression = concept.uri

        collection.add_expressions({'expressions': [concept_expression]}, collection.created_by)

        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.references.first().expression, concept.uri)
        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.concepts.first(), concept)

        concept2 = ConceptFactory(parent=source, sources=[source])
        collection.add_expressions({'expressions': [concept2.uri]}, collection.created_by)

        self.assertEqual(collection.expansion.concepts.count(), 2)
        self.assertEqual(collection.references.count(), 2)

    @patch('core.collections.models.batch_index_resources')
    def test_delete_references(self, batch_index_resources_mock):
        batch_index_resources_mock.apply_async = Mock()
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection.add_expressions({'expressions': [concept1.uri, concept2.uri, mapping.uri]}, collection.created_by)

        self.assertEqual(collection.expansion.concepts.count(), 2)
        self.assertEqual(collection.expansion.mappings.count(), 1)
        self.assertEqual(collection.references.count(), 3)

        collection.delete_references(
            [concept2.get_latest_version().uri, concept2.uri, mapping.get_latest_version().uri, mapping.uri])

        self.assertEqual(collection.expansion.concepts.count(), 1)
        self.assertEqual(collection.expansion.mappings.count(), 0)
        self.assertEqual(collection.references.count(), 1)
        self.assertEqual(collection.expansion.concepts.first().uri, concept1.uri)
        self.assertEqual(collection.references.first().expression, concept1.uri)
        batch_index_resources_mock.apply_async.assert_called()

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

        collection1.add_expressions({'expressions': [concept_expression]}, collection1.created_by)

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
        ch_locale = ConceptNameFactory.build(locale_preferred=True, locale='ch')
        en_locale = ConceptNameFactory.build(locale_preferred=True, locale='en')
        concept = ConceptFactory(names=[ch_locale, en_locale])
        reference = CollectionReference(expression=concept.uri, collection=collection)
        reference.save()

        self.assertEqual(collection.references.count(), 1)

        errors = collection.validate(reference)

        self.assertEqual(
            errors,
            {
                concept.uri: {
                    'errors': [{
                        'description': 'Concept or Mapping reference name must be unique in a collection.',
                        'conflicting_references': [reference.uri]
                    }]
                }
            }
        )

    def test_validate_openmrs_schema_duplicate_locale_type(self):
        ch_locale = ConceptNameFactory.build(locale_preferred=True, locale='ch')
        en_locale = ConceptNameFactory.build(locale_preferred=True, locale='en')
        concept1 = ConceptFactory(names=[ch_locale, en_locale])
        collection = OrganizationCollectionFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept1)
        concept1_reference = CollectionReference(
            expression=concept1.uri, collection=collection, system=concept1.parent.uri, version='HEAD')
        concept1_reference.evaluate()
        concept1_reference.save()

        duplicate_ch_locale = ConceptNameFactory.build(name=ch_locale.name, locale_preferred=True, locale='ch')
        duplicate_en_locale = ConceptNameFactory.build(name=en_locale.name, locale_preferred=True, locale='en')
        concept2 = ConceptFactory(names=[duplicate_ch_locale, duplicate_en_locale])
        concept2_reference = CollectionReference(
            expression=concept2.uri, collection=collection, system=concept2.parent.uri, version='HEAD')

        self.assertEqual(
            collection.validate(concept2_reference),
            {
                concept2.uri: {
                    'errors': [
                        {
                            'description': 'Concept fully specified name must be unique for same collection and '
                                           'locale.',
                            'conflicting_concept_url': concept1.uri,
                            'conflicting_concept_id': concept1.mnemonic,
                            'conflicting_concept_name': concept1.display_name,
                            'conflicting_name_url': f"{concept1.uri}names/{en_locale.id}/",
                            'conflicting_name': en_locale.name,
                            'conflicting_references': [concept1_reference.uri]
                        },
                        {
                            'description': 'Concept fully specified name must be unique for same collection and '
                                           'locale.',
                            'conflicting_concept_url': concept1.uri,
                            'conflicting_concept_id': concept1.mnemonic,
                            'conflicting_concept_name': concept1.display_name,
                            'conflicting_name_url': f"{concept1.uri}names/{ch_locale.id}/",
                            'conflicting_name': ch_locale.name,
                            'conflicting_references': [concept1_reference.uri]
                        },
                        {
                            'description': 'Concept preferred name must be unique for same collection and locale.',
                            'conflicting_concept_url': concept1.uri,
                            'conflicting_concept_id': concept1.mnemonic,
                            'conflicting_concept_name': concept1.display_name,
                            'conflicting_name_url': f"{concept1.uri}names/{en_locale.id}/",
                            'conflicting_name': en_locale.name,
                            'conflicting_references': [concept1_reference.uri]
                        },
                        {
                            'description': 'Concept preferred name must be unique for same collection and locale.',
                            'conflicting_concept_url': concept1.uri,
                            'conflicting_concept_id': concept1.mnemonic,
                            'conflicting_concept_name': concept1.display_name,
                            'conflicting_name_url': f"{concept1.uri}names/{ch_locale.id}/",
                            'conflicting_name': ch_locale.name,
                            'conflicting_references': [concept1_reference.uri]
                        },
                    ]
                }
            }
        )

    def test_validate_openmrs_schema_matching_name_locale(self):
        ch_locale = ConceptNameFactory.build(locale_preferred=False, locale='ch')
        concept1 = ConceptFactory(names=[ch_locale])
        collection = OrganizationCollectionFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        collection.expansion.concepts.add(concept1)
        concept1_reference = CollectionReference(
            expression=concept1.uri, collection=collection, system=concept1.parent.uri, version='HEAD')
        concept1_reference.evaluate()
        concept1_reference.save()

        en_locale1 = ConceptNameFactory.build(locale='en', locale_preferred=False, name='name')
        en_locale2 = ConceptNameFactory.build(locale='en', locale_preferred=True, name='name')
        concept2 = ConceptFactory(names=[en_locale1, en_locale2])
        concept2_reference = CollectionReference(
            expression=concept2.uri, collection=collection, system=concept2.parent.uri, version='HEAD')

        self.assertEqual(
            collection.validate(concept2_reference),
            {
                concept2.uri: {
                    'errors': [
                        {
                            'description': 'Concept fully specified name must be unique for same collection and '
                                           'locale.',
                            'conflicting_concept_url': concept2.uri,
                            'conflicting_concept_id': concept2.mnemonic,
                            'conflicting_concept_name': 'name',
                            'conflicting_name_url': f'{concept2.uri}names/{en_locale1.id}/',
                            'conflicting_name': 'name',
                            'conflicting_references': []
                        }
                    ]
                }
            }
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

    def test_references_distribution(self):
        collection = OrganizationCollectionFactory()
        reference1 = CollectionReference(expression='/foo/concepts/', collection=collection, reference_type='concepts')
        reference2 = CollectionReference(expression='/foo/mappings', collection=collection, reference_type='mappings')
        reference3 = CollectionReference(
            expression='/bar/concepts', collection=collection, reference_type='mappings', include=False)
        reference1.save()
        reference2.save()
        reference3.save()

        self.assertEqual(collection.references.count(), 3)

        distribution = collection.references_distribution

        self.assertEqual(distribution, {'concepts': 1, 'mappings': 2, 'include': 2, 'exclude': 1, 'total': 3})

    def test_referenced_sources_distribution(self):
        self.maxDiff = None
        collection = OrganizationCollectionFactory()
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        source2_v1 = OrganizationSourceFactory(mnemonic=source2.mnemonic, version='v1', organization=source2.parent)
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source2)
        concept3 = ConceptFactory(parent=source2)
        mapping = MappingFactory(parent=source2)
        concept2_latest_version = concept2.get_latest_version()
        concept2_latest_version.sources.add(source2_v1)
        reference1 = CollectionReference(
            expression=concept1.uri, collection=collection, system=source1.uri, code=concept1.mnemonic
        )
        reference2 = CollectionReference(
            expression=concept2_latest_version.uri, collection=collection, system=source2_v1.uri,
            code=concept2.mnemonic, resource_version=concept2_latest_version.version
        )
        reference3 = CollectionReference(
            expression=concept3.uri, collection=collection, system=source2.uri,
            code=concept3.mnemonic
        )
        reference4 = CollectionReference(
            expression=mapping.uri, collection=collection, system=source2.uri, reference_type='mappings'
        )
        reference1.clean()
        reference1.save()
        reference2.clean()
        reference2.save()
        reference3.clean()
        reference3.save()
        reference4.clean()
        reference4.save()

        distribution = collection.referenced_sources_distribution

        self.assertCountEqual(
            distribution,
            [{
                 'id': 'HEAD',
                 'version_url': source1.uri,
                 'type': 'Source Version',
                 'short_code': source1.mnemonic,
                 'released': False,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 1,
                     'mappings': 0,
                     'references': 1
                 }
             }, {
                 'id': 'v1',
                 'version_url': source2_v1.uri,
                 'type': 'Source Version',
                 'short_code': source2.mnemonic,
                 'released': False,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 1,
                     'mappings': 0,
                     'references': 1
                 }
             }, {
                'id': 'HEAD',
                'version_url': source2.uri,
                'type': 'Source Version',
                'short_code': source2.mnemonic,
                'released': False,
                'distribution': {
                    'include_reference': True,
                    'concepts': 1,
                    'mappings': 1,
                    'references': 2
                }
            }]
        )

    def test_referenced_collections_distribution(self):  # pylint: disable=too-many-locals
        self.maxDiff = None
        collection = OrganizationCollectionFactory()
        collection2 = OrganizationCollectionFactory()
        source1 = OrganizationSourceFactory()
        source2 = OrganizationSourceFactory()
        source2_v1 = OrganizationSourceFactory(mnemonic=source2.mnemonic, version='v1', organization=source2.parent)
        concept1 = ConceptFactory(parent=source1)
        concept2 = ConceptFactory(parent=source2)
        concept3 = ConceptFactory(parent=source2)
        mapping = MappingFactory(parent=source2)
        concept2_latest_version = concept2.get_latest_version()
        concept2_latest_version.sources.add(source2_v1)
        reference1 = CollectionReference(
            expression=concept1.uri, collection=collection, system=source1.uri, code=concept1.mnemonic
        )
        reference2 = CollectionReference(
            expression=concept2_latest_version.uri, collection=collection, system=source2_v1.uri,
            code=concept2.mnemonic, resource_version=concept2_latest_version.version
        )
        reference3 = CollectionReference(
            expression=concept3.uri, collection=collection, system=source2.uri,
            code=concept3.mnemonic
        )
        reference4 = CollectionReference(
            expression=mapping.uri, collection=collection, system=source2.uri, reference_type='mappings'
        )
        reference1.clean()
        reference1.save()
        reference2.clean()
        reference2.save()
        reference3.clean()
        reference3.save()
        reference4.clean()
        reference4.save()

        reference5 = CollectionReference(
            expression=collection.uri, collection=collection2, valueset=[collection.uri], reference_type='concepts'
        )
        reference6 = CollectionReference(
            expression=collection.uri, collection=collection2, valueset=[collection.uri], reference_type='mappings'
        )
        reference5.clean()
        reference5.save()
        reference6.clean()
        reference6.save()

        distribution = collection2.referenced_collections_distribution

        self.assertCountEqual(
            distribution,
            [{
                 'id': 'HEAD',
                 'version_url': collection.uri,
                 'type': 'Collection Version',
                 'short_code': collection.mnemonic,
                 'released': False,
                 'autoexpand': True,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 0,  # no expansion
                     'mappings': 0,
                     'references': 2
                 }
             }]
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

        reference = CollectionReference(
            expression=expression, collection=collection, system=concept.parent.uri, version='HEAD')
        reference.full_clean()

        self.assertEqual(len(reference._concepts), 1)  # pylint: disable=protected-access
        self.assertTrue(isinstance(reference._concepts[0], Concept))  # pylint: disable=protected-access
        self.assertEqual(reference._concepts[0].id, concept.id)  # pylint: disable=protected-access

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
        with self.assertRaises(ValidationError) as ex:
            ref = CollectionReference(expression='/concepts/', filter=[{'property': 'bar'}])
            ref.clean()
        self.assertEqual(ex.exception.message_dict, {'filter': ['Invalid filter schema.']})

        ref = CollectionReference(
            expression='/concepts/', filter=[{'property': 'concept_class', 'value': 'foobar', 'op': '='}])
        ref.clean()
        ref = CollectionReference(
            expression='/concepts/', filter=[{'property': 'external_id', 'value': 'foobar', 'op': 'in'}])
        ref.clean()

    def test_mapping_filter_schema(self):
        mapping = MappingFactory()
        MappingDocument().update([mapping])  # to create mappings index in ES

        ref = CollectionReference(expression='/mappings/', filter=None, reference_type='mappings')
        ref.clean()
        ref = CollectionReference(expression='/mappings/', filter='', reference_type='mappings')
        ref.clean()
        ref = CollectionReference(expression='/mappings/', filter=[], reference_type='mappings')
        ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/mappings/', filter=[{}, {}], reference_type='mappings')
            ref.clean()

        with self.assertRaises(ValidationError):
            ref = CollectionReference(expression='/mappings/', filter=[{'foo': 'bar'}], reference_type='mappings')
            ref.clean()
        with self.assertRaises(ValidationError) as ex:
            ref = CollectionReference(expression='/mappings/', filter=[{'property': 'bar'}], reference_type='mappings')
            ref.clean()

        self.assertEqual(ex.exception.message_dict, {'filter': ['Invalid filter schema.']})

        ref = CollectionReference(
            expression='/mappings/',
            reference_type='mappings',
            filter=[{'property': 'map_type', 'value': 'foobar', 'op': '='}])
        ref.clean()
        ref = CollectionReference(
            expression='/mappings/',
            reference_type='mappings',
            filter=[{'property': 'external_id', 'value': 'foobar', 'op': 'in'}])
        ref.clean()

    def test_get_concepts(self):  # pylint: disable=too-many-locals,too-many-statements
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(organization=source.organization, mnemonic=source.mnemonic, version='v1')
        coll1 = OrganizationCollectionFactory()
        coll1_v1 = OrganizationCollectionFactory(organization=coll1.organization, mnemonic=coll1.mnemonic, version='v1')
        coll2 = OrganizationCollectionFactory()
        coll2_v1 = OrganizationCollectionFactory(organization=coll2.organization, mnemonic=coll2.mnemonic, version='v1')

        concept1 = ConceptFactory(parent=source)
        prev_latest_version = concept1.get_latest_version()
        prev_latest_version.is_latest_version = True
        prev_latest_version.save()
        Concept.create_new_version_for(concept1.clone(), {}, concept1.created_by)
        concept1_latest = concept1.get_latest_version()

        concept1.sources.add(source)
        concept1_latest.sources.add(source)
        prev_latest_version.sources.add(source_v1)
        expansion_coll1 = ExpansionFactory(collection_version=coll1)
        coll1.expansion_uri = expansion_coll1.uri
        coll1.save()
        expansion_coll1_v1 = ExpansionFactory(collection_version=coll1_v1)
        coll1_v1.expansion_uri = expansion_coll1_v1.uri
        coll1_v1.save()

        concept2 = ConceptFactory(parent=source)
        expansion_coll1_v1.concepts.add(concept1_latest)

        mapping1 = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)

        reference = CollectionReference(system=source.uri, created_by=source.created_by)
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 2)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1.id, concept2.id])
        )
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by, cascade={'method': 'sourcemappings'}
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 2)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1.id, concept2.id])
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.id)

        reference = CollectionReference(
            system=source.uri,
            code=concept1.mnemonic,
            created_by=source.created_by,
            cascade={'method': 'sourcemappings'}
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1.id])
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.id)

        reference = CollectionReference(
            system=source.uri,
            code=concept1.mnemonic,
            created_by=source.created_by,
            cascade={'method': 'sourcetoconcepts'}
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 2)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1.id, concept2.id])
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.id)

        reference = CollectionReference(
            system=source.uri,
            code=concept1.mnemonic,
            created_by=source.created_by,
            cascade={'method': 'sourcetoconcepts'},
            transform='resourceVersions'
        )
        concepts, mappings = reference.get_concepts()
        concepts = concepts.distinct('id')
        self.assertEqual(concepts.count(), 2)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))),
            sorted([concept1_latest.id, concept2.get_latest_version().id])
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().id, mapping1.get_latest_version().id)

        reference = CollectionReference(
            system=source.uri,
            code=concept1.mnemonic,
            created_by=source.created_by,
            cascade={'method': 'sourcetoconcepts'},
            transform='extensional'
        )
        concepts, mappings = reference.get_concepts()
        concepts = concepts.distinct('id')
        self.assertEqual(concepts.count(), 2)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))),
            sorted([concept1_latest.versioned_object_id, concept2.versioned_object_id])
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(mappings.first().versioned_object_id, mapping1.versioned_object_id)

        reference = CollectionReference(
            system=source.uri,
            valueset=[coll1_v1.uri, coll2_v1.uri],
            created_by=source.created_by
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 0)
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri,
            valueset=[coll1_v1.uri],
            created_by=source.created_by,
            transform='resourceversions'
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1_latest.id])
        )
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri,
            valueset=[coll1_v1.uri],
            created_by=source.created_by,
            transform='extensional'
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 0)  # coll_v1 has latest version of concept1
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri,
            created_by=source.created_by,
            code=concept1.mnemonic,
            transform='resourceversions'
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1_latest.id])
        )
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri,
            created_by=source.created_by,
            code=concept1.mnemonic,
            transform='extensional'
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1_latest.versioned_object_id])
        )
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=source.uri,
            created_by=source.created_by,
            code=concept1.mnemonic,
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([concept1.id])
        )
        self.assertEqual(mappings.count(), 0)

        reference = CollectionReference(
            system=f"{source.uri}|v1",
            created_by=source.created_by,
            code=concept1.mnemonic,
            resource_version=prev_latest_version.version
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 1)
        self.assertEqual(
            sorted(list(concepts.values_list('id', flat=True))), sorted([prev_latest_version.id])
        )
        self.assertEqual(mappings.count(), 0)

        parser = CollectionReferenceParser({'expression': '/concepts/'})
        parser.parse()
        parser.to_reference_structure()
        references = parser.to_objects()
        reference = references[0]
        self.assertEqual(reference.expression, '/concepts/')
        concepts, mappings = reference.get_concepts()
        self.assertEqual(concepts.count(), 0)
        self.assertEqual(mappings.count(), 0)

    def test_build_expression(self):
        self.assertEqual(
            CollectionReference(expression='/foobar/').build_expression(), '/foobar/')
        self.assertEqual(
            CollectionReference(expression='/foobar/', system='http://foo.com').build_expression(), '/foobar/')

        reference = CollectionReference(
            system='http://source.com',
            version='v1',
            valueset=['http//coll.com', 'https://coll-global.com'],
            namespace='/orgs/MyOrg/',
            code='c1',
            reference_type='concepts',
            resource_version='123'
        )
        self.assertEqual(reference.build_expression(), 'http://source.com|v1/concepts/c1/123/')

        reference = CollectionReference(
            system='http://source.com',
            version='v1',
            namespace='/orgs/MyOrg/',
            code='c1',
            reference_type='concepts',
            resource_version='123'
        )
        self.assertEqual(reference.build_expression(), 'http://source.com|v1/concepts/c1/123/')

        reference = CollectionReference(
            system='http://source.com',
            namespace='/orgs/MyOrg/',
            version='v1',
        )
        self.assertEqual(reference.build_expression(), 'http://source.com|v1')

        reference = CollectionReference(
            system='http://source.com',
            namespace='/orgs/MyOrg/',
        )
        self.assertEqual(reference.build_expression(), 'http://source.com')

        reference = CollectionReference(
            system='/orgs/MyOrg/sources/MySource/',
            version='v1',
            valueset=['http//coll.com', 'https://coll-global.com'],
            namespace='/orgs/MyOrg/',
            code='c1',
            reference_type='concepts',
            resource_version='123'
        )
        self.assertEqual(reference.build_expression(), '/orgs/MyOrg/sources/MySource/v1/concepts/c1/123/')

        reference = CollectionReference(
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
        )
        self.assertEqual(reference.build_expression(), '/orgs/MyOrg/sources/MySource/concepts/')

        reference = CollectionReference(
            reference_type='concepts',
        )
        self.assertEqual(reference.build_expression(), '/concepts/')

        reference = CollectionReference(
            reference_type='mappings',
        )
        self.assertEqual(reference.build_expression(), '/mappings/')

        reference = CollectionReference(
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
            filter=[{'property': 'q', 'value': 'foo', 'op': '='}, {'property': 'name', 'value': 'foobar', 'op': '='}]
        )
        self.assertEqual(
            reference.build_expression(), '/orgs/MyOrg/sources/MySource/concepts/?q=foo&name=foobar'
        )

        reference = CollectionReference(
            valueset=['/orgs/MyOrg/collections/Coll/'],
            reference_type='concepts',
            filter=[{'property': 'q', 'value': 'foo', 'op': '='}, {'property': 'name', 'value': 'foobar', 'op': '='}]
        )
        self.assertEqual(
            reference.build_expression(), '/orgs/MyOrg/collections/Coll/concepts/?q=foo&name=foobar'
        )

        reference = CollectionReference(
            valueset=['/orgs/MyOrg/collections/Coll/', '/orgs/MyOrg/collections/Coll1/'],
            reference_type='concepts',
            filter=[{'property': 'q', 'value': 'foo', 'op': '='}, {'property': 'name', 'value': 'foobar', 'op': '='}]
        )
        self.assertEqual(
            reference.build_expression(), '/orgs/MyOrg/collections/Coll/concepts/?q=foo&name=foobar'
        )


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

        added_references, errors = add_references(
            collection.created_by.id,
            {
                'expressions': [obj.get_latest_version().url for obj in [concept1, concept2, mapping2]]
            },
            collection.id,
            'sourcemappings'
        )
        self.assertEqual(len(added_references), 4)
        self.assertEqual(errors, {})
        self.assertListEqual(
            sorted(list(
                collection.references.values_list('expression', flat=True)
            )),
            sorted([
                concept1.get_latest_version().url, concept2.get_latest_version().url,
                mapping1.url, mapping2.get_latest_version().url,
            ])
        )
        self.assertEqual(
            sorted(list(expansion.concepts.values_list('uri', flat=True))),
            sorted([concept1.get_latest_version().url, concept2.get_latest_version().url])
        )
        self.assertEqual(
            sorted(list(expansion.mappings.values_list('uri', flat=True))),
            sorted([mapping1.url, mapping2.get_latest_version().url])
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
        collection.add_expressions(
            {
                'expressions': [concept_latest_version.version_url, mapping_latest_version.version_url]
            },
            collection.created_by
        )

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
        export_collection_task.apply_async.assert_not_called()
        index_children_mock.assert_not_called()

    @patch('core.collections.models.Collection.index_children')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task_with_export(self, export_collection_task, index_children_mock):
        export_collection_task.__name__ = 'export_collection'
        index_children_mock.__name__ = 'index_children'
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        concept = ConceptFactory()
        mapping = MappingFactory()
        concept_latest_version = concept.get_latest_version()
        mapping_latest_version = mapping.get_latest_version()
        collection.add_expressions(
            {
                'expressions': [concept_latest_version.version_url, mapping_latest_version.version_url]
            },
            collection.created_by
        )

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
        export_collection_task.apply_async.assert_called_once_with(
            (collection_v1.id,), queue='default', task_id=ANY, persist_args=True)
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

    def test_get_should_link_repo_versions_criteria(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory()
        mapping = MappingFactory()

        criteria = Expansion.get_should_link_repo_versions_criteria()

        self.assertFalse(Expansion.objects.filter(criteria).exists())

        expansion.concepts.add(concept)

        expansions = Expansion.objects.filter(criteria)
        self.assertTrue(expansions.exists())
        self.assertEqual(expansions.count(), 1)
        self.assertTrue(expansions.first(), expansion)

        expansion.concepts.clear()
        expansion.mappings.add(mapping)

        expansions = Expansion.objects.filter(criteria)
        self.assertTrue(expansions.exists())
        self.assertEqual(expansions.count(), 1)
        self.assertTrue(expansions.first(), expansion)

        expansion.resolved_source_versions.add(mapping.parent)

        expansions = Expansion.objects.filter(criteria)
        self.assertFalse(expansions.exists())

    def test_link_repo_versions(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory()
        mapping = MappingFactory()
        expansion.concepts.add(concept)
        expansion.mappings.add(mapping)

        self.assertFalse(expansion.resolved_source_versions.exists())

        expansion.link_repo_versions()

        self.assertTrue(expansion.resolved_source_versions.exists())
        self.assertEqual(expansion.resolved_source_versions.first(), concept.parent)


class ExpansionParametersTest(OCLTestCase):
    def test_apply_active_only(self):
        ConceptFactory(id=1, retired=False, mnemonic='active')
        ConceptFactory(id=2, retired=True, mnemonic='retired')
        queryset = Concept.objects.filter(id__in=[1, 2])

        result = ExpansionParameters({'activeOnly': True}).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters({'activeOnly': False}).apply(queryset)
        self.assertEqual(result.count(), 2)
        self.assertEqual(
            list(result.order_by('id').values_list('id', flat=True)),
            [1, 2]
        )

    def test_apply_text_filter(self):
        ConceptFactory(id=1, mnemonic='foobar bar')
        ConceptFactory(id=2, mnemonic='bar')
        queryset = Concept.objects.filter(id__in=[1, 2])
        ConceptDocument().update(queryset)  # needed for parallel test execution

        result = ExpansionParameters({'filter': 'foobar'}).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters({'filter': 'foobar'}).apply(queryset)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().id, 1)

        result = ExpansionParameters({'filter': 'bar'}).apply(queryset)
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

    def test_include_system_filter(self):  # pylint: disable=too-many-locals,too-many-statements
        source1 = OrganizationSourceFactory(
            mnemonic='s1', version='HEAD', canonical_url='https://s1.com')
        source2 = OrganizationSourceFactory(
            mnemonic='s2', version='HEAD', canonical_url='https://s2.com')
        source1_v1 = OrganizationSourceFactory(
            mnemonic='s1', version='v1', canonical_url='https://s1.com', organization=source1.organization)
        source1_latest = OrganizationSourceFactory(
            mnemonic='s1', version='latest', canonical_url='https://s1.com', organization=source1.organization,
            released=True)
        source2_latest = OrganizationSourceFactory(
            mnemonic='s2', version='latest', canonical_url='https://s2.com', organization=source2.organization,
            released=True)
        concept1 = ConceptFactory(mnemonic='c1', parent=source1)
        concept2 = ConceptFactory(mnemonic='c1', parent=source2)
        initial_version = concept1.get_latest_version()

        errors = Concept.create_new_version_for(
            concept1.clone(),
            {
                'extras': 'c1.1',
                'names': [{
                              'locale': 'en',
                              'name': 'English',
                              'locale_preferred': True
                          }]
            },
            concept1.created_by
        )
        self.assertEqual(errors, {})
        concept1_v1 = concept1.get_latest_version()
        errors = Concept.create_new_version_for(
            concept1.clone(),
            {
                'extras': 'c1.2',
                'names': [{
                              'locale': 'en',
                              'name': 'English',
                              'locale_preferred': True
                          }]
            },
            concept1.created_by
        )
        self.assertEqual(errors, {})
        concept1_latest = concept1.get_latest_version()

        initial_version.sources.set([source1_v1])
        concept1_v1.sources.set([source1_latest])
        concept1_latest.sources.set([source1])
        concept2.sources.set([source2, source2_latest])

        collection = OrganizationCollectionFactory(
            mnemonic='c1', canonical_url='http://c1.com', version='HEAD')
        expansion = ExpansionFactory(mnemonic='e1', collection_version=collection)

        ref1 = CollectionReference(system='https://s1.com', code=concept1.mnemonic, namespace=source1.organization.uri)
        expansion.parameters['system-version'] = 'https://s1.com|v1'
        expansion.add_references(ref1)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().mnemonic, initial_version.mnemonic)   # v1 source version concept c1

        expansion.concepts.clear()

        ref2 = CollectionReference(system='https://s1.com', code=concept1.mnemonic, namespace=source1.organization.uri)
        expansion.parameters['system-version'] = None
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept1_v1.id)  # latest source version concept c1

        expansion.concepts.clear()

        ref2 = CollectionReference(
            system='https://s1.com', code=concept1.mnemonic, version='HEAD', namespace=source1.organization.uri)
        expansion.parameters['system-version'] = None
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept1.id)  # HEAD source version concept c1

        expansion.concepts.clear()

        ref2 = CollectionReference(system='https://s1.com', code=concept1.mnemonic, namespace=source1.organization.uri)
        expansion.parameters['system-version'] = None
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept1_v1.id)  # latest source version concept c1

        expansion.concepts.clear()

        ref2 = CollectionReference(
            system='https://s1.com', code=concept1.mnemonic, resource_version=concept1_v1.version,
            namespace=source1.organization.uri)
        expansion.parameters['system-version'] = 'https://s1.com|v1'
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept1_v1.id)  # locked resource version

        expansion.concepts.clear()

        ref2 = CollectionReference(
            system='https://s2.com', code=concept2.mnemonic, namespace=source2.organization.uri)
        expansion.parameters['system-version'] = 'https://s1.com|v1'
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept2.id)

        ref2 = CollectionReference(
            system='https://s2.com', code=concept2.mnemonic, namespace=source2.organization.uri)
        expansion.parameters['system-version'] = 'https://s1.com|v1,https://s2.com|latest'
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 1)
        self.assertEqual(expansion.concepts.first().id, concept2.id)

        ref2 = CollectionReference(
            system='https://s1.com', code=concept1.mnemonic, namespace=source1.organization.uri)
        expansion.parameters['system-version'] = 'https://s1.com|v1,https://s1.com|latest'
        expansion.add_references(ref2)

        self.assertEqual(expansion.concepts.count(), 2)
        self.assertEqual(
            list(expansion.concepts.order_by('id').values_list('mnemonic', flat=True)),
            sorted([concept2.mnemonic, concept1_latest.mnemonic])
        )

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


class CollectionReferenceExpressionStringParserTest(OCLTestCase):
    @staticmethod
    def get_structure(**kwargs):
        parser = CollectionReferenceExpressionStringParser(**kwargs)
        parser.parse()
        return parser.to_reference_structure()[0]

    def test_parse_concept_expressions(self):
        reference = self.get_structure(expression='/concepts/')
        self.assertEqual(
            reference,
            {
                'expression': '/concepts/',
                'system': None,
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/concepts/?q=foobar&conceptClass=drug')
        self.assertEqual(
            reference,
            {
                'expression': '/concepts/?q=foobar&conceptClass=drug',
                'system': None,
                'valueset': None,
                'filter': [
                    {'property': 'q', 'value': 'foobar', 'op': '='},
                    {'property': 'conceptClass', 'value': 'drug', 'op': '='},
                ],
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/concepts/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/concepts/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/v1/concepts/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': 'v1',
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/concepts/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/v1/concepts/1234/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': 'v1',
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/concepts/1234/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(
            expression='/orgs/MyOrg/sources/MySource/concepts/1234/', cascade='sourceToConcept')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/concepts/1234/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': 'sourceToConcept',
                'reference_type': 'concepts',
                'version': None,
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/concepts/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/concepts/?q=foo&external_id=alpha,beta',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': [
                    {
                        'property': 'q',
                        'value': 'foo',
                        'op': '='
                    },
                    {
                        'property': 'external_id',
                        'value': 'alpha,beta',
                        'op': '='
                    },
                ],
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/concepts/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/concepts/?q=foo&external_id=alpha,beta',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/'
                ],
                'filter': [
                    {
                        'property': 'q',
                        'value': 'foo',
                        'op': '='
                    },
                    {
                        'property': 'external_id',
                        'value': 'alpha,beta',
                        'op': '='
                    },
                ],
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )
        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/v1/concepts/1234/',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/concepts/1234/3456/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/v1/concepts/1234/3456/',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                'filter': None,
                'cascade': None,
                'reference_type': 'concepts',
                'version': None,
                'code': '1234',
                'resource_version': '3456',
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

    def test_parse_mapping_expressions(self):
        reference = self.get_structure(expression='/mappings/')
        self.assertEqual(
            reference,
            {
                'expression': '/mappings/',
                'system': None,
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/mappings/?q=foobar&mapType=Q-AND-A')
        self.assertEqual(
            reference,
            {
                'expression': '/mappings/?q=foobar&mapType=Q-AND-A',
                'system': None,
                'valueset': None,
                'filter': [
                    {
                        'property': 'q',
                        'value': 'foobar',
                        'op': '='
                    },
                    {
                        'property': 'mapType',
                        'value': 'Q-AND-A',
                        'op': '='
                    },
                ],
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/mappings/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/mappings/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/v1/mappings/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': 'v1',
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/v1/mappings/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/v1/mappings/1234/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': 'v1',
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/mappings/1234/',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/sources/MySource/mappings/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/sources/MySource/mappings/?q=foo&external_id=alpha,beta',
                'system': '/orgs/MyOrg/sources/MySource/',
                'valueset': None,
                'filter': [
                    {
                        'property': 'q',
                        'value': 'foo',
                        'op': '='
                    },
                    {
                        'property': 'external_id',
                        'value': 'alpha,beta',
                        'op': '='
                    },
                ],
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/mappings/?q=foo&external_id=alpha,beta')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/mappings/?q=foo&external_id=alpha,beta',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/'
                ],
                'filter': [
                    {
                        'property': 'q',
                        'value': 'foo',
                        'op': '='
                    },
                    {
                        'property': 'external_id',
                        'value': 'alpha,beta',
                        'op': '='
                    },
                ],
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': None,
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )
        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/v1/mappings/1234/',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': '1234',
                'resource_version': None,
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )

        reference = self.get_structure(expression='/orgs/MyOrg/collections/Coll/v1/mappings/1234/3456/')
        self.assertEqual(
            reference,
            {
                'expression': '/orgs/MyOrg/collections/Coll/v1/mappings/1234/3456/',
                'system': None,
                'valueset': [
                    '/orgs/MyOrg/collections/Coll/|v1'
                ],
                'filter': None,
                'cascade': None,
                'reference_type': 'mappings',
                'version': None,
                'code': '1234',
                'resource_version': '3456',
                'transform': None,
                'created_by': None,
                'display': None,
                'include': True
            }
        )


class CollectionReferenceSourceAllExpressionParserTest(OCLTestCase):
    @staticmethod
    def get_structure(**kwargs):
        parser = CollectionReferenceSourceAllExpressionParser(**kwargs)
        parser.parse()
        return parser.to_reference_structure()

    def test_parse(self):
        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/",
                'concepts': "*",
                'mappings': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/concepts/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'concepts',
                    'version': None,
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
                {
                    'expression': '/users/Me/sources/MySource/mappings/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'mappings',
                    'version': None,
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                }
            ]
        )
        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/v1/",
                'concepts': "*",
                'mappings': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/v1/concepts/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'concepts',
                    'version': 'v1',
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
                {
                    'expression': '/users/Me/sources/MySource/v1/mappings/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'mappings',
                    'version': 'v1',
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                }
            ]
        )

        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/v1/",
                'concepts': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/v1/concepts/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'concepts',
                    'version': 'v1',
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
            ]
        )
        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/v1/",
                'mappings': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/v1/mappings/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'mappings',
                    'version': 'v1',
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
            ]
        )
        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/",
                'concepts': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/concepts/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'concepts',
                    'version': None,
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
            ]
        )
        reference = self.get_structure(
            expression={
                'uri': "/users/Me/sources/MySource/",
                'mappings': "*"
            })
        self.assertEqual(
            reference,
            [
                {
                    'expression': '/users/Me/sources/MySource/mappings/',
                    'system': '/users/Me/sources/MySource/',
                    'valueset': None,
                    'filter': None,
                    'cascade': None,
                    'reference_type': 'mappings',
                    'version': None,
                    'code': None,
                    'resource_version': None,
                    'transform': None,
                    'created_by': None,
                    'display': None,
                    'include': True
                },
            ]
        )


class CollectionReferenceOldStyleToExpandedStructureParserTest(OCLTestCase):
    @staticmethod
    def get_expanded_references(**kwargs):
        parser = CollectionReferenceOldStyleToExpandedStructureParser(**kwargs)
        parser.parse()
        parser.to_reference_structure()
        return parser.to_objects()

    def test_parse_string_expression_generic(self):  # pylint: disable=too-many-statements
        references = self.get_expanded_references(
            expression=[
                "/orgs/MyOrg/sources/MySource/concepts/c-1234/",
            ]
        )
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        references = self.get_expanded_references(
            expression="/orgs/MyOrg/sources/MySource/concepts/c-1234/"
        )
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        references = self.get_expanded_references(
            expression={
                'expressions': [
                    "/orgs/MyOrg/sources/MySource/concepts/c-1234/",
                    "/orgs/MyOrg/sources/MySource/mappings/m-1234/",
                    "/users/Me/sources/MySource/concepts/?q=foobar",
                    "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule",
                    "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A",
                ]
            }
        )
        self.assertEqual(len(references), 5)

        reference = references[0]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        reference = references[1]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/m-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "m-1234")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        reference = references[2]
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/concepts/?q=foobar")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(reference.filter, [{'property': 'q', 'value': 'foobar', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

        reference = references[3]
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(
            reference.filter,
            [{'property': 'q', 'value': 'foobar', 'op': '='}, {'property': 'datatype', 'value': 'rule', 'op': '='}]
        )
        self.assertEqual(reference.version, 'v1')
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

        reference = references[4]
        self.assertEqual(reference.expression, "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A")
        self.assertEqual(reference.valueset, ["/users/Me/collections/MyColl/|v1"])
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertEqual(
            reference.filter,
            [{'property': 'mapType', 'value': 'Q-AND-A', 'op': '='}]
        )
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.system)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

    def test_parse_string_expression_concepts_mappings_explicit(self):  # pylint: disable=too-many-statements
        references = self.get_expanded_references(
            expression={
                'concepts': [
                    "/orgs/MyOrg/sources/MySource/concepts/c-1234/",
                    "/users/Me/sources/MySource/concepts/?q=foobar",
                    "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule",
                ],
                'mappings': [
                    "/orgs/MyOrg/sources/MySource/mappings/m-1234/",
                    "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A",
                ]
            }
        )
        self.assertEqual(len(references), 5)

        reference = references[0]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        reference = references[1]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/concepts/?q=foobar")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(reference.filter, [{'property': 'q', 'value': 'foobar', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

        reference = references[2]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(
            reference.filter,
            [{'property': 'q', 'value': 'foobar', 'op': '='}, {'property': 'datatype', 'value': 'rule', 'op': '='}])
        self.assertEqual(reference.version, 'v1')
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

        reference = references[3]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/m-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "m-1234")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        reference = references[4]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A")
        self.assertEqual(reference.valueset, ["/users/Me/collections/MyColl/|v1"])
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertEqual(
            reference.filter,
            [{'property': 'mapType', 'value': 'Q-AND-A', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.system)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)

    def test_parse_source_all_resources_expression(self):
        references = self.get_expanded_references(
            expression={
                'concepts': "*",
                'mappings': "*",
                'uri': '/orgs/MyOrg/sources/MySource/'
            }
        )
        self.assertEqual(len(references), 2)

        reference = references[0]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.code)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)

        reference = references[1]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.code)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)


class CollectionReferenceParserTest(OCLTestCase):
    @staticmethod
    def get_expanded_references(**kwargs):
        parser = CollectionReferenceParser(**kwargs)
        parser.parse()
        parser.to_reference_structure()
        return parser.to_objects()

    def test_parse_string_expression_generic(self):  # pylint: disable=too-many-statements
        references = self.get_expanded_references(
            expression={
                'expressions': [
                    "/orgs/MyOrg/sources/MySource/concepts/c-1234/",
                    "/orgs/MyOrg/sources/MySource/mappings/m-1234/",
                    "/users/Me/sources/MySource/concepts/?q=foobar",
                    "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule",
                    "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A",
                    "/orgs/MyOrg/sources/MySource/concepts/foo%252Fbar/",
                ]
            }
        )
        self.assertEqual(len(references), 6)

        reference = references[0]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest concept "c-1234" from MyOrg/MySource')

        reference = references[1]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/m-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "m-1234")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest mapping "m-1234" from MyOrg/MySource')

        reference = references[2]
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/concepts/?q=foobar")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(reference.filter, [{'property': 'q', 'value': 'foobar', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(reference.translation, 'Include latest concepts from Me/MySource containing "foobar"')

        reference = references[3]
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(
            reference.filter,
            [{'property': 'q', 'value': 'foobar', 'op': '='}, {'property': 'datatype', 'value': 'rule', 'op': '='}])
        self.assertEqual(reference.version, 'v1')
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(
            reference.translation,
            'Include concepts from version "v1" of Me/MySource containing "foobar" & having datatype equal to "rule"'
        )

        reference = references[4]
        self.assertEqual(reference.expression, "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A")
        self.assertEqual(reference.valueset, ["/users/Me/collections/MyColl/|v1"])
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertEqual(
            reference.filter,
            [{'property': 'mapType', 'value': 'Q-AND-A', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.system)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(
            reference.translation,
            'Include mappings from version "v1" of Me/MyColl having mapType equal to "Q-AND-A"'
        )

        reference = references[5]
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/foo%252Fbar/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "foo%252Fbar")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest concept "foo/bar" from MyOrg/MySource')

    def test_parse_string_expression_concepts_mappings_explicit(self):  # pylint: disable=too-many-statements
        references = self.get_expanded_references(
            expression={
                'concepts': [
                    "/orgs/MyOrg/sources/MySource/concepts/c-1234/",
                    "/users/Me/sources/MySource/concepts/?q=foobar",
                    "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule",
                ],
                'mappings': [
                    "/orgs/MyOrg/sources/MySource/mappings/m-1234/",
                    "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A",
                ]
            }
        )
        self.assertEqual(len(references), 5)

        reference = references[0]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/c-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "c-1234")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest concept "c-1234" from MyOrg/MySource')

        reference = references[1]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/concepts/?q=foobar")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(reference.filter, [{'property': 'q', 'value': 'foobar', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(reference.translation, 'Include latest concepts from Me/MySource containing "foobar"')

        reference = references[2]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/sources/MySource/v1/concepts/?q=foobar&datatype=rule")
        self.assertEqual(reference.system, "/users/Me/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertEqual(
            reference.filter,
            [{'property': 'q', 'value': 'foobar', 'op': '='}, {'property': 'datatype', 'value': 'rule', 'op': '='}])
        self.assertEqual(reference.version, 'v1')
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(
            reference.translation,
            'Include concepts from version "v1" of Me/MySource containing "foobar" & having datatype equal to "rule"'
        )

        reference = references[3]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/m-1234/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.code, "m-1234")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest mapping "m-1234" from MyOrg/MySource')

        reference = references[4]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/users/Me/collections/MyColl/v1/mappings/?mapType=Q-AND-A")
        self.assertEqual(reference.valueset, ["/users/Me/collections/MyColl/|v1"])
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertEqual(
            reference.filter,
            [{'property': 'mapType', 'value': 'Q-AND-A', 'op': '='}])
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.system)
        self.assertIsNone(reference.cascade)
        self.assertIsNone(reference.code)
        self.assertEqual(
            reference.translation,
            'Include mappings from version "v1" of Me/MyColl having mapType equal to "Q-AND-A"'
        )

    def test_parse_source_all_resources_expression(self):
        references = self.get_expanded_references(
            expression={
                'concepts': "*",
                'mappings': "*",
                'uri': '/orgs/MyOrg/sources/MySource/'
            }
        )
        self.assertEqual(len(references), 2)

        reference = references[0]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/concepts/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.reference_type, 'concepts')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.code)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest concepts from MyOrg/MySource')

        reference = references[1]
        self.assertTrue(isinstance(reference, CollectionReference))
        self.assertEqual(reference.expression, "/orgs/MyOrg/sources/MySource/mappings/")
        self.assertEqual(reference.system, "/orgs/MyOrg/sources/MySource/")
        self.assertEqual(reference.reference_type, 'mappings')
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.code)
        self.assertIsNone(reference.valueset)
        self.assertIsNone(reference.filter)
        self.assertIsNone(reference.cascade)
        self.assertEqual(reference.translation, 'Include latest mappings from MyOrg/MySource')

    def test_parse_new_style(self):  # pylint: disable=too-many-statements
        references = self.get_expanded_references(
            expression={"url": "http://hl7.org/fhir/ValueSet/my-valueset|0.8", "code": "1948"}
        )
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].system, "http://hl7.org/fhir/ValueSet/my-valueset|0.8")
        self.assertEqual(references[0].code, "1948")
        self.assertIsNone(references[0].version)
        self.assertEqual(
            references[0].build_expression(), "http://hl7.org/fhir/ValueSet/my-valueset|0.8/concepts/1948/")

        references = self.get_expanded_references(
            expression={"system": "http://hl7.org/fhir/CodeSystem/my-codeystem", "code": "1948"}
        )
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(references[0].code, "1948")
        self.assertIsNone(references[0].version)
        self.assertEqual(
            references[0].build_expression(), "http://hl7.org/fhir/CodeSystem/my-codeystem/concepts/1948/")
        self.assertEqual(
            references[0].translation,
            'Include latest concept "1948" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        references = self.get_expanded_references(
            expression={
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem",
              "version": "0.8",
              "namespace": "/orgs/foobar/",
              "filter": [
                {
                  "property": "datatype",
                  "op": "=",
                  "value": "Numeric"
                }
              ],
              "valueSet": [
                "http://hl7.org/fhir/ValueSet/my-valueset1",
                "http://hl7.org/fhir/ValueSet/my-valueset2"
              ]
            }
        )
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(references[0].version, "0.8")
        self.assertEqual(references[0].namespace, "/orgs/foobar/")
        self.assertEqual(
            references[0].valueset,
            ["http://hl7.org/fhir/ValueSet/my-valueset1", "http://hl7.org/fhir/ValueSet/my-valueset2"]
        )
        self.assertEqual(
            references[0].filter,
            [
                {
                    "property": "datatype",
                    "op": "=",
                    "value": "Numeric"
                }
            ]
        )
        self.assertEqual(references[0].reference_type, 'concepts')
        self.assertIsNone(references[0].code)
        self.assertEqual(
            references[0].build_expression(),
            "http://hl7.org/fhir/CodeSystem/my-codeystem|0.8/concepts/?datatype=Numeric"
        )
        self.assertEqual(
            references[0].translation,
            'Include concepts from version "0.8" of http://hl7.org/fhir/CodeSystem/my-codeystem intersection with http://hl7.org/fhir/ValueSet/my-valueset1 intersection with http://hl7.org/fhir/ValueSet/my-valueset2 having datatype equal to "Numeric"'  # pylint: disable=line-too-long
        )

        references = self.get_expanded_references(
            expression={
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem",
              "filter": [
                {
                  "property": "datatype",
                  "op": "=",
                  "value": "Numeric"
                }
              ],
            }
        )
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertIsNone(references[0].version)
        self.assertEqual(
            references[0].filter,
            [
                {
                    "property": "datatype",
                    "op": "=",
                    "value": "Numeric"
                }
            ]
        )
        self.assertIsNone(references[0].code)
        self.assertEqual(references[0].reference_type, 'concepts')
        self.assertEqual(
            references[0].build_expression(),
            "http://hl7.org/fhir/CodeSystem/my-codeystem/concepts/?datatype=Numeric"
        )
        self.assertEqual(
            references[0].translation,
            'Include latest concepts from http://hl7.org/fhir/CodeSystem/my-codeystem having datatype equal to "Numeric"'  # pylint: disable=line-too-long
        )

        references = self.get_expanded_references(
            expression={
              "valueSet": "http://hl7.org/fhir/ValueSet/my-valueset1",
              "filter": [
                {
                  "property": "datatype",
                  "op": "=",
                  "value": "Numeric"
                }
              ],
            }
        )
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].valueset, ["http://hl7.org/fhir/ValueSet/my-valueset1"])
        self.assertIsNone(references[0].version)
        self.assertEqual(
            references[0].filter,
            [
                {
                    "property": "datatype",
                    "op": "=",
                    "value": "Numeric"
                }
            ]
        )
        self.assertIsNone(references[0].code)
        self.assertEqual(references[0].reference_type, 'concepts')
        self.assertEqual(
            references[0].build_expression(),
            "http://hl7.org/fhir/ValueSet/my-valueset1/concepts/?datatype=Numeric"
        )
        self.assertEqual(
            references[0].translation,
            'Include latest concepts from http://hl7.org/fhir/ValueSet/my-valueset1 having datatype equal to "Numeric"'
        )

        references = self.get_expanded_references(
            expression={
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem",
              "concept": [
                {"code": "1948", "display": "abcd"},
                {"code": "1234"}
              ],
              "mapping": ["93", "urjdk"]
            }
        )

        self.assertEqual(len(references), 4)
        reference = references[0]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "1948")
        self.assertEqual(reference.display, "abcd")
        self.assertEqual(reference.reference_type, "concepts")
        self.assertIsNone(reference.version)
        self.assertEqual(
            reference.translation,
            'Include latest concept "1948" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        reference = references[1]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "1234")
        self.assertEqual(reference.reference_type, "concepts")
        self.assertIsNone(reference.display)
        self.assertIsNone(reference.version)
        self.assertEqual(
            reference.translation,
            'Include latest concept "1234" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        reference = references[2]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "93")
        self.assertEqual(reference.reference_type, "mappings")
        self.assertIsNone(reference.version)
        self.assertEqual(
            reference.translation,
            'Include latest mapping "93" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        reference = references[3]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "urjdk")
        self.assertEqual(reference.reference_type, "mappings")
        self.assertIsNone(reference.version)
        self.assertEqual(
            reference.translation,
            'Include latest mapping "urjdk" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        references = self.get_expanded_references(expression={
            "valueSet": "http://hl7.org/fhir/ValueSet/my-valueset1",
            "code": "1948"
        })
        self.assertEqual(len(references), 1)
        self.assertIsNone(references[0].system)
        self.assertIsNone(references[0].version)
        self.assertIsNone(references[0].resource_version)
        self.assertEqual(references[0].valueset, ["http://hl7.org/fhir/ValueSet/my-valueset1"])
        self.assertEqual(references[0].reference_type, "concepts")
        self.assertEqual(references[0].code, "1948")
        self.assertEqual(references[0].build_expression(), "http://hl7.org/fhir/ValueSet/my-valueset1/concepts/1948/")
        self.assertEqual(
            references[0].translation,
            'Include latest concept "1948" from http://hl7.org/fhir/ValueSet/my-valueset1'
        )

        references = self.get_expanded_references(
            expression={
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem",
              "concept": "1948",
              "mapping": "93"
            }
        )

        self.assertEqual(len(references), 2)
        reference = references[0]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "1948")
        self.assertEqual(reference.reference_type, "concepts")
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.display)
        self.assertEqual(
            references[0].translation,
            'Include latest concept "1948" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        reference = references[1]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "93")
        self.assertEqual(reference.reference_type, "mappings")
        self.assertIsNone(reference.version)
        self.assertEqual(
            references[1].translation,
            'Include latest mapping "93" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        references = self.get_expanded_references(
            expression=[{
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem",
              "concept": "1948",
            }, {
              "system": "http://hl7.org/fhir/CodeSystem/my-codeystem2",
              "mapping": "93"
            }]
        )

        self.assertEqual(len(references), 2)
        reference = references[0]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem")
        self.assertEqual(reference.code, "1948")
        self.assertEqual(reference.reference_type, "concepts")
        self.assertIsNone(reference.version)
        self.assertIsNone(reference.display)
        self.assertEqual(
            reference.translation,
            'Include latest concept "1948" from http://hl7.org/fhir/CodeSystem/my-codeystem'
        )

        reference = references[1]
        self.assertEqual(reference.system, "http://hl7.org/fhir/CodeSystem/my-codeystem2")
        self.assertEqual(reference.code, "93")
        self.assertEqual(reference.reference_type, "mappings")
        self.assertIsNone(reference.version)
        self.assertEqual(
            reference.translation,
            'Include latest mapping "93" from http://hl7.org/fhir/CodeSystem/my-codeystem2'
        )


class ExpansionConceptsIndexViewTest(OCLAPITestCase):
    @patch('core.collections.views.index_expansion_concepts')
    def test_post_200(self, index_expansion_concepts_task_mock):
        index_expansion_concepts_task_mock.__name__ = 'index_expansion_concepts_task_mock'
        admin = UserProfile.objects.get(username='ocladmin')
        collection = UserCollectionFactory(user=admin, created_by=admin, updated_by=admin)
        expansion = ExpansionFactory(collection_version=collection, created_by=admin)
        collection.expansion_uri = expansion.uri
        collection.save()

        response = self.client.post(
            f"{expansion.uri}concepts/index/",
            {},
            HTTP_AUTHORIZATION=f"Token {admin.get_token()}",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'index_expansion_concepts_task_mock',
                'queue': 'indexing',
                'username': 'ocladmin',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': [],
                'message': None
            }
        )
        index_expansion_concepts_task_mock.apply_async.assert_called_once_with(
            (expansion.id,), task_id=ANY, queue='indexing')


class ExpansionMappingsIndexViewTest(OCLAPITestCase):
    @patch('core.collections.views.index_expansion_mappings')
    def test_post_200(self, index_expansion_mappings_task_mock):
        index_expansion_mappings_task_mock.__name__ = 'index_expansion_mappings_task_mock'
        admin = UserProfile.objects.get(username='ocladmin')
        collection = UserCollectionFactory(user=admin, created_by=admin, updated_by=admin)
        expansion = ExpansionFactory(collection_version=collection, created_by=admin)
        collection.expansion_uri = expansion.uri
        collection.save()

        response = self.client.post(
            f"{expansion.uri}mappings/index/",
            {},
            HTTP_AUTHORIZATION=f"Token {admin.get_token()}",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                'id': ANY,
                'task': ANY,
                'state': 'PENDING',
                'name': 'index_expansion_mappings_task_mock',
                'queue': 'indexing',
                'username': 'ocladmin',
                'created_at': ANY,
                'started_at': None,
                'finished_at': None,
                'runtime': None,
                'summary': None,
                'children': [],
                'message': None
            }
        )
        index_expansion_mappings_task_mock.apply_async.assert_called_once_with(
            (expansion.id,), task_id=ANY, queue='indexing')
