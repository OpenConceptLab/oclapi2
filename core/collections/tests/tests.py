from celery_once import AlreadyQueued
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import override_settings
from mock import patch, Mock
from mock.mock import ANY
from rest_framework import status
from rest_framework.response import Response

from core.collections.constants import SOURCE_TO_CONCEPTS, TRANSFORM_TO_RESOURCE_VERSIONS
from core.collections.documents import CollectionDocument
from core.collections.models import CollectionReference, Collection, Expansion
from core.collections.models import ExpansionParameters, ExpansionSystemParameter
from core.collections.parsers import CollectionReferenceExpressionStringParser, \
    CollectionReferenceSourceAllExpressionParser, CollectionReferenceOldStyleToExpandedStructureParser, \
    CollectionReferenceParser
from core.collections.serializers import CollectionVersionListSerializer, CollectionCreateSerializer, \
    CollectionDetailSerializer, CollectionVersionDetailSerializer, CollectionReferenceSerializer, \
    CollectionSummaryFieldDistributionSerializer
from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory, UserCollectionFactory
from core.collections.utils import is_mapping, is_concept, is_version_specified, \
    get_concept_by_expression
from core.common.constants import OPENMRS_VALIDATION_SCHEMA, ACCESS_TYPE_NONE
from core.common.tasks import add_references, seed_children_to_new_version
from core.common.tasks import update_collection_active_concepts_count
from core.common.tasks import update_collection_active_mappings_count
from core.common.tests import OCLTestCase, OCLAPITestCase
from core.common.utils import to_owner_uri, get_falsy_values
from core.concepts.documents import ConceptDocument
from core.concepts.models import Concept
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.mappings.documents import MappingDocument
from core.mappings.models import Mapping
from core.mappings.tests.factories import MappingFactory
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.sources.tests.factories import OrganizationSourceFactory
from core.tasks.models import Task
from core.users.models import UserProfile
from core.users.tests.factories import UserProfileFactory


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

    @patch('core.common.models.delete_s3_objects', Mock())
    @patch('core.collections.models.Collection.clear_cache')
    def test_delete_head_clears_cache_for_all_versions(self, clear_cache_mock):
        head = OrganizationCollectionFactory()
        OrganizationCollectionFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')
        OrganizationCollectionFactory(mnemonic=head.mnemonic, organization=head.organization, version='v2')

        head.delete(force=True)

        # once for HEAD itself (post_delete_actions) and once each for v1 and v2
        self.assertEqual(clear_cache_mock.call_count, 3)

    @patch('core.common.models.delete_s3_objects')
    def test_delete_head_clears_export_and_external_export_s3_objects_for_all_versions(
            self, delete_s3_objects_mock
    ):
        from core.repos.models import RepoExternalExport
        head = OrganizationCollectionFactory()
        v1 = OrganizationCollectionFactory(mnemonic=head.mnemonic, organization=head.organization, version='v1')
        v2 = OrganizationCollectionFactory(mnemonic=head.mnemonic, organization=head.organization, version='v2')
        RepoExternalExport.objects.create(resource=v1, key='ext1', file_path='v1/external/report.zip')
        RepoExternalExport.objects.create(resource=v2, key='ext2', file_path='v2/external/report.zip')

        expected_paths = sorted([
            head.get_version_export_path(suffix=None),
            v1.get_version_export_path(suffix=None),
            v2.get_version_export_path(suffix=None),
            'v1/external/report.zip',
            'v2/external/report.zip',
        ])

        head.delete(force=True, sync=True)

        called_paths = sorted(call.args[0] for call in delete_s3_objects_mock.call_args_list)
        self.assertEqual(called_paths, expected_paths)

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
        self.assertEqual(concept.references.count(), 1)

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
        self.assertEqual(concept.references.count(), 1)

    def test_add_expressions_unexpected_error_is_json_serializable(self):
        collection = OrganizationCollectionFactory()
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, sources=[source])

        with patch.object(
                CollectionReference, 'save',
                side_effect=AttributeError("'NoneType' object has no attribute 'foo'")
        ):
            _, errors = collection.add_expressions({'expressions': [concept.uri]}, collection.created_by)

        self.assertEqual(errors, {concept.uri: "'NoneType' object has no attribute 'foo'"})

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
                 'version': 'HEAD',
                 'version_url': source1.uri,
                 'type': 'Source Version',
                 'short_code': source1.mnemonic,
                 'released': False,
                 'description': ANY,
                 'name': ANY,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 1,
                     'mappings': 0,
                     'references': 1
                 }
             }, {
                 'id': 'v1',
                 'version': 'v1',
                 'version_url': source2_v1.uri,
                 'type': 'Source Version',
                 'short_code': source2.mnemonic,
                 'released': False,
                 'description': ANY,
                 'name': ANY,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 1,
                     'mappings': 0,
                     'references': 1
                 }
             }, {
                'id': 'HEAD',
                'version': 'HEAD',
                'version_url': source2.uri,
                'type': 'Source Version',
                'short_code': source2.mnemonic,
                'released': False,
                'description': ANY,
                'name': ANY,
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
                 'version': 'HEAD',
                 'version_url': collection.uri,
                 'type': 'Collection Version',
                 'short_code': collection.mnemonic,
                 'released': False,
                 'autoexpand': True,
                 'description': ANY,
                 'name': ANY,
                 'distribution': {
                     'include_reference': True,
                     'concepts': 0,  # no expansion
                     'mappings': 0,
                     'references': 2
                 }
             }]
        )

    def test_save_triggers_resolve_url_registry_entries_when_canonical_url_dirty(self):
        from core.url_registry.models import URLRegistry  # pylint: disable=import-outside-toplevel
        collection = OrganizationCollectionFactory(canonical_url='http://old.canonical.url')
        URLRegistry.objects.create(url='http://foo-registry.com', repo=collection, is_active=True)

        with patch('core.collections.models.resolve_url_registry_entries') as resolve_mock:
            collection.canonical_url = 'http://new.canonical.url'
            collection.save()

        resolve_mock.apply_async.assert_called_once_with(
            (collection.id, collection.resource_type), queue='default', permanent=False
        )

    def test_validate_openmrs_schema_no_concepts_resolved_returns_none(self):
        collection = OrganizationCollectionFactory(custom_validation_schema=OPENMRS_VALIDATION_SCHEMA)
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        reference = CollectionReference(expression='/concepts/', collection=collection, reference_type='concepts')

        self.assertIsNone(collection.validate(reference))

    def test_get_brief_serializer(self):
        from core.collections.serializers import (  # pylint: disable=import-outside-toplevel
            CollectionVersionMinimalSerializer, CollectionMinimalSerializer)
        head_collection = OrganizationCollectionFactory()
        version_collection = OrganizationCollectionFactory(
            organization=head_collection.organization, mnemonic=head_collection.mnemonic, version='v1')

        self.assertEqual(head_collection.get_brief_serializer(), CollectionMinimalSerializer)
        self.assertEqual(version_collection.get_brief_serializer(), CollectionVersionMinimalSerializer)

    def test_get_resource_facet_filters(self):
        collection = OrganizationCollectionFactory(version='v1')
        expansion = ExpansionFactory(collection_version=collection, mnemonic='e1')
        collection.expansion_uri = expansion.uri
        collection.save()

        filters = collection._get_resource_facet_filters()  # pylint: disable=protected-access

        self.assertEqual(
            filters,
            {
                'collection': collection.mnemonic,
                'collection_owner_url': to_owner_uri(collection.uri),
                'expansion': 'e1',
                'retired': False,
                'collection_version': 'v1',
            }
        )

        filters = collection._get_resource_facet_filters({'retired': True})  # pylint: disable=protected-access
        self.assertTrue(filters['retired'])

    def test_get_tasks(self):
        collection = OrganizationCollectionFactory(version='v1')
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        self.assertIsNone(collection.get_export_task())
        self.assertIsNone(collection.get_index_concepts_task())
        self.assertIsNone(collection.get_index_mappings_task())
        self.assertIsNone(collection.get_seed_new_version_task())
        self.assertEqual(
            collection.get_tasks(),
            {
                'seeded_concepts': None, 'seeded_mappings': None,
                'indexed_concepts': None, 'indexed_mappings': None, 'exported': None,
            }
        )

        export_task = Task.objects.create(
            id='task-export', name='core.common.tasks.export_collection', args=[collection.id],
            created_by=collection.created_by
        )
        index_concepts_task = Task.objects.create(
            id='task-index-concepts', name='core.collections.models.index_expansion_concepts',
            args=[expansion.id], created_by=collection.created_by
        )
        index_mappings_task = Task.objects.create(
            id='task-index-mappings', name='core.collections.models.index_expansion_mappings',
            args=[expansion.id], created_by=collection.created_by
        )
        seed_task = Task.objects.create(
            id='task-seed', name='core.collections.models.seed_children_to_expansion',
            args=[collection.id], created_by=collection.created_by
        )

        self.assertEqual(collection.get_export_task().id, export_task.id)
        self.assertEqual(collection.get_index_concepts_task().id, index_concepts_task.id)
        self.assertEqual(collection.get_index_mappings_task().id, index_mappings_task.id)
        self.assertEqual(collection.get_seed_new_version_task().id, seed_task.id)

        tasks = collection.get_tasks()
        self.assertEqual(tasks['exported'].id, export_task.id)
        self.assertEqual(tasks['indexed_concepts'].id, index_concepts_task.id)
        self.assertEqual(tasks['indexed_mappings'].id, index_mappings_task.id)
        self.assertEqual(tasks['seeded_concepts'].id, seed_task.id)
        self.assertEqual(tasks['seeded_mappings'].id, seed_task.id)


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

    def test_get_resolved_repo_versions_serialized(self):
        collection = OrganizationCollectionFactory()
        system = OrganizationSourceFactory()
        valueset = OrganizationCollectionFactory()

        system_reference = CollectionReference(
            expression=f'{system.uri}concepts/foo/', collection=collection, system=system.uri, version='HEAD'
        )
        valueset_reference = CollectionReference(
            expression=f'{valueset.uri}concepts/foo/', collection=collection, valueset=[valueset.uri]
        )
        combined_reference = CollectionReference(
            expression=f'{system.uri}concepts/foo/', collection=collection, system=system.uri,
            version='HEAD', valueset=[valueset.uri]
        )
        empty_reference = CollectionReference(expression='/concepts/foo/', collection=collection)

        system_data = system_reference.get_resolved_repo_versions_serialized()
        valueset_data = valueset_reference.get_resolved_repo_versions_serialized()
        combined_data = combined_reference.get_resolved_repo_versions_serialized()

        self.assertEqual([item['version_url'] for item in system_data], [system.uri])
        self.assertEqual([item['version_url'] for item in valueset_data], [valueset.uri])
        self.assertCountEqual([item['version_url'] for item in combined_data], [system.uri, valueset.uri])
        self.assertEqual(empty_reference.get_resolved_repo_versions_serialized(), [])

    def test_get_resolved_repo_versions_serialized_uses_cache(self):
        collection = OrganizationCollectionFactory()
        system = OrganizationSourceFactory()
        first_reference = CollectionReference(
            expression=f'{system.uri}concepts/foo/', collection=collection, system=system.uri, version='HEAD'
        )
        second_reference = CollectionReference(
            expression=f'{system.uri}concepts/bar/', collection=collection, system=system.uri, version='HEAD'
        )
        system_version_cache = {}

        with patch.object(Source, 'resolve_reference_expression', wraps=Source.resolve_reference_expression) as mock:
            first_reference.get_resolved_repo_versions_serialized(system_version_cache=system_version_cache)
            second_reference.get_resolved_repo_versions_serialized(system_version_cache=system_version_cache)

        self.assertEqual(mock.call_count, 1)

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

    def test_get_static_references_criteria(self):
        collection = OrganizationCollectionFactory()
        static_ref = CollectionReference(expression='/concepts/', collection=collection, resource_version='1')
        static_ref.save()
        non_static_ref = CollectionReference(expression='/concepts/other/', collection=collection)
        non_static_ref.save()

        matched_ids = list(
            CollectionReference.objects.filter(
                CollectionReference.get_static_references_criteria()
            ).values_list('id', flat=True)
        )

        self.assertIn(static_ref.id, matched_ids)
        self.assertNotIn(non_static_ref.id, matched_ids)

    def test_parent(self):
        collection = OrganizationCollectionFactory()
        reference = CollectionReference(collection=collection)
        self.assertEqual(reference.parent, collection)

    def test_can_compute_against_other_system_version(self):
        self.assertTrue(CollectionReference(system='http://sys.com').can_compute_against_other_system_version)
        self.assertFalse(
            CollectionReference(system='http://sys.com', version='v1').can_compute_against_other_system_version)
        self.assertFalse(
            CollectionReference(
                system='http://sys.com', resource_version='1').can_compute_against_other_system_version)
        self.assertTrue(CollectionReference().can_compute_against_other_system_version)

    def test_can_compute_against_system_version(self):
        source = OrganizationSourceFactory()
        other_source = OrganizationSourceFactory()

        reference = CollectionReference(system=source.uri, created_by=source.created_by)

        self.assertTrue(reference.can_compute_against_system_version(source))
        self.assertFalse(reference.can_compute_against_system_version(other_source))
        self.assertFalse(reference.can_compute_against_system_version(None))

        pinned_reference = CollectionReference(system=source.uri, version='v1', created_by=source.created_by)
        self.assertFalse(pinned_reference.can_compute_against_system_version(source))

    def test_get_concepts_applies_es_filter(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, concept_class='Diagnosis')
        other_concept = ConceptFactory(parent=source, concept_class='Procedure')
        ConceptDocument().update([concept, other_concept])

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by,
            filter=[{'property': 'concept_class', 'value': 'Diagnosis', 'op': '='}]
        )
        concepts, mappings = reference.get_concepts()

        self.assertEqual(list(concepts.values_list('id', flat=True)), [concept.id])
        self.assertEqual(mappings.count(), 0)

        no_match_reference = CollectionReference(
            system=source.uri, created_by=source.created_by,
            filter=[{'property': 'concept_class', 'value': 'NoSuchClass', 'op': '='}]
        )
        no_match_concepts, _ = no_match_reference.get_concepts()
        self.assertEqual(no_match_concepts.count(), 0)

    def test_get_concepts_applies_es_filter_with_case_switch_extras_and_unknown_property(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source, concept_class='Diagnosis', extras={'nested': 'val1'})
        other_concept = ConceptFactory(parent=source, concept_class='Procedure')
        ConceptDocument().update([concept, other_concept])

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by, version='HEAD',
            filter=[
                {'property': 'conceptClass', 'value': 'Diagnosis', 'op': '='},
                {'property': 'extras.nested', 'value': 'val1', 'op': '='},
                {'property': 'nonsense_prop', 'value': 'x', 'op': '='},
            ]
        )
        concepts, _mappings = reference.get_concepts()

        self.assertEqual(list(concepts.values_list('id', flat=True)), [concept.id])

    def test_get_concepts_applies_es_filter_with_q_property(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        ConceptDocument().update([concept])

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by,
            filter=[{'property': 'q', 'value': concept.mnemonic, 'op': '='}]
        )

        concepts, _mappings = reference.get_concepts()

        self.assertIsNotNone(concepts)

    def test_get_concepts_returns_empty_queryset_when_filters_return_none(self):
        source = OrganizationSourceFactory()
        ConceptFactory(parent=source)

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by,
            filter=[{'property': 'concept_class', 'value': 'Diagnosis', 'op': '='}]
        )

        with patch.object(CollectionReference, '_apply_filters', return_value=None):
            concepts, mappings = reference.get_concepts()

        self.assertEqual(concepts.count(), 0)
        self.assertEqual(mappings.count(), 0)

    def test_get_mappings_applies_es_filter(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(
            from_concept=concept1, to_concept=concept2, parent=source, map_type='SAME-AS')
        other_mapping = MappingFactory(
            from_concept=concept1, to_concept=concept2, parent=source, map_type='NARROWER-THAN')
        MappingDocument().update([mapping, other_mapping])

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by, reference_type='mappings',
            filter=[{'property': 'map_type', 'value': 'SAME-AS', 'op': '='}]
        )
        mappings = reference.get_mappings()

        self.assertEqual(list(mappings.values_list('id', flat=True)), [mapping.id])

    def test_get_mappings_returns_empty_queryset_when_filters_return_none(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by, reference_type='mappings',
            filter=[{'property': 'map_type', 'value': 'SAME-AS', 'op': '='}]
        )

        with patch.object(CollectionReference, '_apply_filters', return_value=None):
            mappings = reference.get_mappings()

        self.assertEqual(mappings.count(), 0)

    def test_apply_transform_to_versioned_for_pinned_system(self):
        source = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            organization=source.organization, mnemonic=source.mnemonic, version='v1', released=True)
        concept = ConceptFactory(parent=source)
        concept_latest = concept.get_latest_version()
        concept_latest.sources.add(source_v1)

        reference = CollectionReference(
            system=f'{source.uri}|v1', created_by=source.created_by, transform='extensional'
        )
        concepts, _mappings = reference.get_concepts()

        self.assertEqual(reference.version, 'HEAD')
        self.assertEqual(concepts.count(), 1)
        self.assertEqual(concepts.first().id, concept.versioned_object_id)

    def test_apply_cascade_skips_already_traversed_concept_uri(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        Concept.create_new_version_for(concept.clone(), {}, concept.created_by)
        concept_v1 = concept.get_latest_version()

        reference = CollectionReference(
            system=source.uri, created_by=source.created_by, cascade={'method': 'sourcemappings'})
        concept_queryset = Concept.objects.filter(id__in=[concept.id, concept_v1.id])

        concepts, _mappings = reference._apply_cascade(  # pylint: disable=protected-access
            concept_queryset, Mapping.objects.none(), reference.resolve_system_version, []
        )

        self.assertTrue(concepts.filter(id=concept.id).exists())
        self.assertTrue(concepts.filter(id=concept_v1.id).exists())

    def test_get_repo_and_resource_version_static_transform_head(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        reference = CollectionReference(system=source.uri, created_by=source.created_by, transform='resourceVersions')

        repo_version, resource_version = (
            reference._CollectionReference__get_repo_and_resource_version(  # pylint: disable=protected-access
                concept, True, False
            )
        )

        self.assertEqual(repo_version, 'HEAD')
        self.assertEqual(resource_version, concept.version)

    def test_generate_references_extensional_transform_with_non_head_repo_version(self):
        source = OrganizationSourceFactory()
        # Not released, so an unpinned system reference still resolves to HEAD (avoids the
        # should_transform_to_versioned() mutation of self.version inside get_concepts()).
        source_v1 = OrganizationSourceFactory(
            organization=source.organization, mnemonic=source.mnemonic, version='v1')
        concept = ConceptFactory(parent=source)
        concept.sources.add(source_v1)

        reference = CollectionReference(
            system=source.uri, code=concept.mnemonic, created_by=source.created_by,
            cascade={'method': 'sourcetoconcepts'}, transform='extensional'
        )

        references = reference.generate_references()

        concept_refs = [ref for ref in references if ref.reference_type == 'concepts']
        self.assertEqual(len(concept_refs), 1)
        self.assertEqual(concept_refs[0].version, 'v1')
        self.assertIsNone(concept_refs[0].resource_version)

    def test_get_concept_cascade_params_async_removes_max_results(self):
        reference = CollectionReference(cascade={'method': 'sourcemappings', 'max_results': 100})
        reference._async = True  # pylint: disable=protected-access

        params = reference.get_concept_cascade_params()

        self.assertIsNone(params['max_results'])

    def test_has_param_in_filter(self):
        reference = CollectionReference(filter=[{'property': 'excludeWildcard', 'value': 'false', 'op': '='}])
        self.assertTrue(reference.has_param_in_filter('excludewildcard', get_falsy_values()))
        self.assertFalse(reference.has_param_in_filter('excludefuzzy', get_falsy_values()))

    def test_apply_search_wildcard_and_fuzzy(self):
        reference = CollectionReference(
            filter=[
                {'property': 'excludeWildcard', 'value': 'false', 'op': '='},
                {'property': 'excludeFuzzy', 'value': 'false', 'op': '='},
                {'property': 'searchMapCodes', 'value': 'false', 'op': '='},
            ]
        )
        search = ConceptDocument.search()

        result = reference._apply_search(search, 'diabetes', ConceptDocument)  # pylint: disable=protected-access

        self.assertIsNotNone(result)

    def test_get_concepts_valueset_only_no_system(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept.get_latest_version())

        reference = CollectionReference(valueset=[collection.uri], created_by=collection.created_by)
        concepts, mappings = reference.get_concepts()

        self.assertEqual(list(concepts.values_list('id', flat=True)), [concept.get_latest_version().id])
        self.assertEqual(mappings.count(), 0)

    def test_resolve_valueset_versions_with_unresolved_resolves_explicit_and_evaluated(self):
        collection = OrganizationCollectionFactory()
        valueset_collection = OrganizationCollectionFactory()
        valueset_collection_v1 = OrganizationCollectionFactory(
            organization=valueset_collection.organization, mnemonic=valueset_collection.mnemonic, version='v1')

        reference = CollectionReference(
            collection=collection,
            valueset=[valueset_collection.uri, valueset_collection_v1.uri, None]
        )

        explicit, evaluated, unresolved = reference.resolve_valueset_versions_with_unresolved

        self.assertEqual(unresolved, [])
        self.assertEqual([version.id for version in evaluated], [valueset_collection.id])
        self.assertEqual([version.id for version in explicit], [valueset_collection_v1.id])

    def test_clean_builds_expression_when_none(self):
        reference = CollectionReference(expression=None, reference_type='concepts')
        reference.clean()
        self.assertEqual(reference.expression, '/concepts/')

    def test_filter_to_querystring_invalid_and_empty(self):
        self.assertIsNone(CollectionReference(filter='not-a-list').filter_to_querystring())
        self.assertIsNone(CollectionReference(filter=[]).filter_to_querystring())

    def test_get_allowed_filter_properties(self):
        concept_props = CollectionReference(reference_type='concepts').get_allowed_filter_properties()
        self.assertIn('concept_class', concept_props)
        self.assertIn('q', concept_props)

        mapping_props = CollectionReference(reference_type='mappings').get_allowed_filter_properties()
        self.assertIn('map_type', mapping_props)
        self.assertIn('q', mapping_props)

    def test_get_allowed_filter_properties_but_need_case_switch(self):
        props = list(
            CollectionReference(reference_type='concepts').get_allowed_filter_properties_but_need_case_switch())
        self.assertIn('conceptClass', props)
        self.assertIn('excludeWildcard', props)

    def test_get_related_uris(self):
        source = OrganizationSourceFactory()
        concept = ConceptFactory(parent=source)
        reference = CollectionReference(system=source.uri, created_by=source.created_by, code=concept.mnemonic)

        uris = reference.get_related_uris()

        self.assertEqual(uris, [concept.uri])

    def test_get_resolved_repo_versions_serialized_uses_valueset_cache(self):
        collection = OrganizationCollectionFactory()
        valueset_collection = OrganizationCollectionFactory()
        first_reference = CollectionReference(
            expression=f'{valueset_collection.uri}concepts/foo/', collection=collection,
            valueset=[valueset_collection.uri]
        )
        second_reference = CollectionReference(
            expression=f'{valueset_collection.uri}concepts/bar/', collection=collection,
            valueset=[valueset_collection.uri]
        )
        valueset_version_cache = {}

        with patch.object(
                Collection, 'resolve_reference_expression', wraps=Collection.resolve_reference_expression) as mock:
            first_reference.get_resolved_repo_versions_serialized(valueset_version_cache=valueset_version_cache)
            second_reference.get_resolved_repo_versions_serialized(valueset_version_cache=valueset_version_cache)

        self.assertEqual(mock.call_count, 1)

    def test_apply_filters_returns_queryset_unchanged_when_no_filter(self):
        reference = CollectionReference(filter=None)
        queryset = Concept.objects.none()

        result = reference._apply_filters(queryset, Concept)  # pylint: disable=protected-access

        self.assertIs(result, queryset)


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


class CollectionReferenceParserCascadeTest(OCLTestCase):
    def test_to_objects_cascades_related_references_now(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(parent=source, from_concept=concept1, to_concept=concept2, map_type='Same As')

        collection = OrganizationCollectionFactory()
        user = collection.created_by

        references = Collection.parse_expressions(
            {'expressions': [concept1.uri]}, user, cascade=SOURCE_TO_CONCEPTS, transform='extensional'
        )

        expressions = [reference.expression for reference in references]
        self.assertIn(concept2.uri, expressions)
        self.assertIn(mapping.uri, expressions)
        self.assertTrue(len(references) > 1)


class CollectionSerializersTest(OCLTestCase):
    @staticmethod
    def _request(query_string=''):
        request = Mock()
        request.query_params = QueryDict(query_string)
        request.path = '/collections/'
        return request

    def test_get_external_exports(self):
        collection = OrganizationCollectionFactory()
        self.assertEqual(CollectionVersionListSerializer.get_external_exports(collection), [])

    def test_prepare_object_supported_locales_as_comma_separated_string(self):
        serializer = CollectionCreateSerializer()
        collection = serializer.prepare_object(
            {'mnemonic': 'coll-locales', 'name': 'Coll Locales', 'supported_locales': 'en,es,fr'})
        self.assertEqual(collection.supported_locales, ['en', 'es', 'fr'])

    def test_prepare_object_invalid_json_string_kept_as_is(self):
        serializer = CollectionCreateSerializer()
        collection = serializer.prepare_object(
            {'mnemonic': 'coll-json', 'name': 'Coll Json', 'jurisdiction': 'not-json{'})
        self.assertEqual(collection.jurisdiction, 'not-json{')

    def test_update_invalid_expansion_url_skips_persist(self):
        collection = OrganizationCollectionFactory()
        collection.expansion_uri = f'{collection.uri}expansions/does-not-exist/'
        collection.save()

        serializer = CollectionDetailSerializer()
        serializer._errors = {}  # pylint: disable=protected-access
        result = serializer.update(collection, {})

        self.assertIn('expansion_url', serializer._errors)  # pylint: disable=protected-access
        self.assertIs(result, collection)

    def test_create_serializer_validate_invalid_released_value(self):
        serializer = CollectionCreateSerializer(data={
            'id': 'coll-invalid-released', 'name': 'Coll Invalid Released', 'released': 'notabool'
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('released', serializer.errors)

    def test_get_distribution(self):
        collection = OrganizationCollectionFactory()
        serializer = CollectionSummaryFieldDistributionSerializer(
            context={'request': self._request('distribution=datatype,unknownField')})
        self.assertEqual(serializer.get_distribution(collection), {'datatype': []})

    def test_get_client_configs(self):
        collection = OrganizationCollectionFactory()
        serializer_included = CollectionDetailSerializer(
            context={'request': self._request('includeClientConfigs=true')})
        self.assertEqual(serializer_included.get_client_configs(collection), [])

        serializer_excluded = CollectionDetailSerializer()
        self.assertIsNone(serializer_excluded.get_client_configs(collection))

    def test_get_states_included(self):
        collection = OrganizationCollectionFactory()
        serializer = CollectionVersionDetailSerializer(
            context={'request': self._request('includeStates=true')})
        self.assertEqual(serializer.get_states(collection), collection.states)

    def test_get_tasks_included(self):
        collection = OrganizationCollectionFactory()
        serializer = CollectionVersionDetailSerializer(
            context={'request': self._request('includeTasks=true')})
        self.assertEqual(serializer.get_tasks(collection), collection.get_tasks_info())

    def test_get_resolved_repo_versions_not_included(self):
        serializer = CollectionReferenceSerializer()
        self.assertIsNone(serializer.get_resolved_repo_versions(Mock()))


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

        self.assertEqual(len(added_references), 3)
        self.assertEqual(errors, {})
        self.assertListEqual(
            sorted(list(
                collection.references.values_list('expression', flat=True)
            )),
            sorted([
                concept1.get_latest_version().url, concept2.get_latest_version().url,
                mapping2.get_latest_version().url,
            ])
        )
        self.assertEqual(mapping1.references.count(), 1)
        self.assertEqual(
            sorted(list(expansion.concepts.values_list('uri', flat=True))),
            sorted([concept1.get_latest_version().url, concept2.get_latest_version().url])
        )
        self.assertEqual(
            sorted(list(expansion.mappings.values_list('uri', flat=True))),
            sorted([mapping1.url, mapping2.get_latest_version().url])
        )
        self.assertEqual(expansion.unresolved_repo_versions, [])
        self.assertEqual(expansion.explicit_collection_versions.count(), 0)
        self.assertEqual(expansion.explicit_source_versions.count(), 3)
        self.assertEqual(
            sorted(list(expansion.explicit_source_versions.values_list('uri', flat=True))),
            sorted([concept1.parent.uri, concept2.parent.uri, mapping2.parent.uri])
        )

        added_references, errors = add_references(
            collection.created_by.id,
            [
                {'system': 'http://foo-system.com', 'namespace': 'barbar', 'code': 'bar'},
                {'system': 'http://foo-system2.com|v1',
                 'valueset': ['http://foo-valueset.com', '/orgs/Org/collections/Collection/123/'],
                 'code': 'bar'},
            ],
            collection.id,
            'sourcemappings'
        )
        expansion.refresh_from_db()

        self.assertEqual(len(added_references), 2)
        self.assertEqual(errors, {})
        self.assertListEqual(
            sorted(list(
                collection.references.values_list('expression', flat=True)
            )),
            sorted([
                concept1.get_latest_version().url,
                concept2.get_latest_version().url,
                mapping2.get_latest_version().url,
                'http://foo-system.com/concepts/bar/',
                'http://foo-system2.com|v1/concepts/bar/'
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
        self.assertEqual(
            expansion.unresolved_repo_versions,
            [
                {'url': 'http://foo-system.com', 'type': 'reference.system', 'version': None, 'namespace': 'barbar'},
                {'url': 'http://foo-valueset.com', 'type': 'reference.valueset', 'namespace': None},
                {'url': '/orgs/Org/collections/Collection/123/', 'type': 'reference.valueset', 'namespace': None},
                {'url': 'http://foo-system2.com|v1', 'type': 'reference.system', 'version': None, 'namespace': None}
            ]
        )
        self.assertEqual(expansion.explicit_collection_versions.count(), 0)
        self.assertEqual(expansion.explicit_source_versions.count(), 3)
        self.assertEqual(
            sorted(list(expansion.explicit_source_versions.values_list('uri', flat=True))),
            sorted([concept1.parent.uri, concept2.parent.uri, mapping2.parent.uri])
        )

    @patch('core.common.tasks.export_collection')
    def test_seed_children_task(self, export_collection_task):
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

    @patch('core.collections.models.index_expansion_mappings')
    @patch('core.collections.models.index_expansion_concepts')
    @patch('core.common.tasks.export_collection')
    def test_seed_children_task_with_export(
            self, export_collection_task, index_expansion_concepts_task, index_expansion_mappings_task):
        export_collection_task.__name__ = 'export_collection'
        index_expansion_concepts_task.__name__ = 'index_expansion_concepts'
        index_expansion_mappings_task.__name__ = 'index_expansion_mappings'
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

        index_expansion_concepts_task.assert_called()
        index_expansion_mappings_task.assert_called()

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
        seed_children_to_expansion_mock.__name__ = 'seed_children_to_expansion'
        collection = OrganizationCollectionFactory()

        expansion = Expansion.persist(index=False, collection_version=collection)

        self.assertIsNotNone(expansion.id)
        self.assertEqual(expansion.id, expansion.mnemonic)
        self.assertEqual(expansion.collection_version, collection)
        seed_children_to_expansion_mock.assert_called_once_with(expansion.id, False)

    @patch('core.collections.models.seed_children_to_expansion')
    def test_persist_with_mnemonic(self, seed_children_to_expansion_mock):
        seed_children_to_expansion_mock.__name__ = 'seed_children_to_expansion'
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

        expansion.explicit_source_versions.add(mapping.parent)

        expansions = Expansion.objects.filter(criteria)
        self.assertFalse(expansions.exists())

    def test_link_repo_versions(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory()
        mapping = MappingFactory()
        expansion.concepts.add(concept)
        expansion.mappings.add(mapping)

        self.assertFalse(expansion.explicit_source_versions.exists())

        expansion.link_repo_versions()

        self.assertTrue(expansion.explicit_source_versions.exists())
        self.assertEqual(expansion.explicit_source_versions.first(), concept.parent)

    def test_get_resolved_repo_version_diff_with_latest_updates(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)

        # No repo versions linked yet — diff should be empty
        self.assertEqual(expansion.get_resolved_repo_version_diff_with_latest_updates(), {})

        # HEAD must exist so resolve_reference_expression can find the latest released version
        source_head = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v1', released=True)
        source_v2 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v2', released=True)
        expansion.explicit_source_versions.add(source_v1)

        diff = expansion.get_resolved_repo_version_diff_with_latest_updates()

        # source_v1 has a newer released version (source_v2), so it should appear in the diff
        self.assertEqual(len(diff), 1)
        self.assertIn(source_v1.url, diff)
        self.assertEqual(diff[source_v1.url], source_v2.url)

        # A source version that is already the latest released should not appear in the diff
        source2_head = OrganizationSourceFactory()
        source2_v1 = OrganizationSourceFactory(
            mnemonic=source2_head.mnemonic, organization=source2_head.organization, version='v1', released=True)
        expansion.explicit_source_versions.add(source2_v1)

        diff = expansion.get_resolved_repo_version_diff_with_latest_updates()

        self.assertEqual(len(diff), 1)
        self.assertIn(source_v1.url, diff)
        self.assertNotIn(source2_v1.url, diff)

    def test_batch_index(self):
        collection = OrganizationCollectionFactory(version='v1')
        expansion = ExpansionFactory(collection_version=collection, mnemonic='e1')
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        expansion.concepts.set([concept1, concept2])
        expansion.mappings.set([mapping])

        with override_settings(TEST_MODE=False):
            with patch.object(ConceptDocument, '_bulk') as concept_bulk_mock:
                expansion.batch_index(expansion.concepts, ConceptDocument)
                self.assertEqual(concept_bulk_mock.call_count, 1)
                actions = list(concept_bulk_mock.call_args[0][0])
                self.assertEqual(len(actions), 2)
                for action in actions:
                    self.assertEqual(action['_op_type'], 'update')
                    self.assertEqual(action['_index'], 'concepts')
                    self.assertIn(action['_id'], [concept1.id, concept2.id])
                    self.assertEqual(action['script']['params']['expansion'], [expansion.mnemonic])
                    self.assertEqual(
                        action['script']['params']['collection_version'], [expansion.collection_version_name])
                    self.assertEqual(
                        action['script']['params']['collection'], [expansion.collection_version_mnemonic])
                    self.assertEqual(
                        action['script']['params']['collection_url'], [expansion.collection_version_url])
                    self.assertEqual(
                        action['script']['params']['collection_owner_url'], [expansion.owner_url])
                    self.assertEqual(action['retry_on_conflict'], 3)
                    self.assertNotIn('upsert', action)
                    self.assertNotIn('scripted_upsert', action)

            with patch.object(MappingDocument, '_bulk') as mapping_bulk_mock:
                expansion.batch_index(expansion.mappings, MappingDocument)
                self.assertEqual(mapping_bulk_mock.call_count, 1)
                actions = list(mapping_bulk_mock.call_args[0][0])
                self.assertEqual(len(actions), 1)
                self.assertEqual(actions[0]['_id'], mapping.id)
                self.assertEqual(actions[0]['script']['params']['expansion'], [expansion.mnemonic])

    def test_batch_index_empty_queryset(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)

        with patch.object(ConceptDocument, '_bulk') as bulk_mock:
            expansion.batch_index(Concept.objects.none(), ConceptDocument)
            bulk_mock.assert_not_called()

    def test_parent(self):
        collection = Collection(mnemonic='coll')
        self.assertEqual(Expansion(collection_version=collection).parent, collection)

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.index_expansion_concepts')
    def test_index_concepts_not_test_mode(self, index_expansion_concepts_mock):
        index_expansion_concepts_mock.__name__ = 'index_expansion_concepts'
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory()
        expansion.concepts.add(concept)

        expansion.index_concepts()

        index_expansion_concepts_mock.apply_async.assert_called_once_with(
            (expansion.id, 1, None), task_id=ANY, queue='indexing', persist_args=True)
        self.assertTrue(Task.objects.filter(name='index_expansion_concepts').exists())

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.index_expansion_concepts')
    def test_index_concepts_not_test_mode_already_queued_deletes_task(self, index_expansion_concepts_mock):
        index_expansion_concepts_mock.__name__ = 'index_expansion_concepts'
        index_expansion_concepts_mock.apply_async.side_effect = AlreadyQueued(60)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept = ConceptFactory()
        expansion.concepts.add(concept)

        task_count_before = Task.objects.count()
        expansion.index_concepts()

        self.assertEqual(Task.objects.count(), task_count_before)

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.index_expansion_mappings')
    def test_index_mappings_not_test_mode(self, index_expansion_mappings_mock):
        index_expansion_mappings_mock.__name__ = 'index_expansion_mappings'
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        expansion.mappings.add(mapping)

        expansion.index_mappings()

        index_expansion_mappings_mock.apply_async.assert_called_once_with(
            (expansion.id, 1, None), task_id=ANY, queue='indexing', persist_args=True)
        self.assertTrue(Task.objects.filter(name='index_expansion_mappings').exists())

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.index_expansion_mappings')
    def test_index_mappings_not_test_mode_already_queued_deletes_task(self, index_expansion_mappings_mock):
        index_expansion_mappings_mock.__name__ = 'index_expansion_mappings'
        index_expansion_mappings_mock.apply_async.side_effect = AlreadyQueued(60)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        concept1 = ConceptFactory()
        concept2 = ConceptFactory(parent=concept1.parent)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=concept1.parent)
        expansion.mappings.add(mapping)

        task_count_before = Task.objects.count()
        expansion.index_mappings()

        self.assertEqual(Task.objects.count(), task_count_before)

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.readd_references_to_expansion_on_references_removal')
    @patch('core.collections.models.batch_index_resources')
    def test_delete_references_not_test_mode_queues_readd_task(self, batch_index_mock, readd_task_mock):
        batch_index_mock.apply_async = Mock()
        collection = OrganizationCollectionFactory()
        concept1 = ConceptFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.set([concept1])
        reference = CollectionReference(expression=concept1.uri, collection=collection)
        reference.save()
        reference.concepts.set([concept1])

        expansion.delete_references(reference)

        readd_task_mock.apply_async.assert_called_once_with(
            (expansion.id, [reference.id]), queue='default', permanent=False)

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.batch_index_resources')
    def test_delete_expressions_not_test_mode_queues_batch_index(self, batch_index_mock):
        collection = OrganizationCollectionFactory()
        concept = ConceptFactory()
        mapping = MappingFactory()
        expansion = ExpansionFactory(collection_version=collection)
        expansion.concepts.set([concept])
        expansion.mappings.set([mapping])

        expansion.delete_expressions([concept.url, mapping.url])

        self.assertEqual(batch_index_mock.apply_async.call_count, 2)
        batch_index_mock.apply_async.assert_any_call(
            ('concept', {'uri__in': [concept.url]}), queue='indexing', permanent=False)
        batch_index_mock.apply_async.assert_any_call(
            ('mapping', {'uri__in': [mapping.url]}), queue='indexing', permanent=False)

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.seed_children_to_expansion')
    def test_persist_not_test_mode_queues_seed_children_task(self, seed_children_mock):
        seed_children_mock.__name__ = 'seed_children_to_expansion'
        collection = OrganizationCollectionFactory()

        expansion = Expansion.persist(index=False, collection_version=collection)

        seed_children_mock.apply_async.assert_called_once_with(
            (expansion.id, False), queue='indexing', task_id=ANY, persist_args=True)

    def test_get_resolved_repo_version_diff_skips_duplicate_url(self):
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        source_head = OrganizationSourceFactory()
        source_v1 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v1', released=True)
        source_v2 = OrganizationSourceFactory(
            mnemonic=source_head.mnemonic, organization=source_head.organization, version='v2', released=True)
        expansion.explicit_source_versions.add(source_v1)
        expansion.evaluated_source_versions.add(source_v1)

        diff = expansion.get_resolved_repo_version_diff_with_latest_updates()

        self.assertEqual(diff, {source_v1.url: source_v2.url})

    def test_filter_queryset_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            ExpansionSystemParameter.filter_queryset(
                Concept.objects.none(), Source.objects.none(), Collection.objects.none())

    @override_settings(TEST_MODE=False)
    @patch('core.collections.models.batch_index_resources')
    def test_add_references_excludes_via_queryset_with_include_system_versions(self, batch_index_mock):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection, mnemonic='e1')
        expansion.parameters = {'system-version': source.uri}
        expansion.save()
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept1.get_latest_version())
        expansion.mappings.add(mapping)

        exclude_concept_ref = CollectionReference(
            expression=concept1.uri, collection=collection, system=source.uri,
            code=concept1.mnemonic, include=False
        )
        exclude_concept_ref.evaluate()
        exclude_concept_ref.save()
        exclude_mapping_ref = CollectionReference(
            expression=mapping.uri, collection=collection, system=source.uri,
            code=mapping.mnemonic, reference_type='mappings', include=False
        )
        exclude_mapping_ref.evaluate()
        exclude_mapping_ref.save()

        expansion.add_references(
            collection.references.all(), index=True, is_adding_all=False, force_reevaluate=True
        )

        self.assertTrue(batch_index_mock.apply_async.called)
        self.assertEqual(expansion.concepts.count(), 0)
        self.assertEqual(expansion.mappings.count(), 0)

    def test_add_references_excludes_in_test_mode(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        mapping = MappingFactory(from_concept=concept1, to_concept=concept2, parent=source)
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection, mnemonic='e1')
        collection.expansion_uri = expansion.uri
        collection.save()
        expansion.concepts.add(concept1.get_latest_version())
        expansion.mappings.add(mapping)

        exclude_concept_ref = CollectionReference(
            expression=concept1.uri, collection=collection, system=source.uri,
            code=concept1.mnemonic, include=False
        )
        exclude_concept_ref.evaluate()
        exclude_concept_ref.save()
        exclude_mapping_ref = CollectionReference(
            expression=mapping.uri, collection=collection, system=source.uri,
            code=mapping.mnemonic, reference_type='mappings', include=False
        )
        exclude_mapping_ref.evaluate()
        exclude_mapping_ref.save()

        expansion.add_references(
            collection.references.all(), index=True, is_adding_all=False, force_reevaluate=True
        )

        self.assertEqual(expansion.concepts.count(), 0)
        self.assertEqual(expansion.mappings.count(), 0)

    def test_add_references_reference_without_system_resolves_none_system_version(self):
        collection = OrganizationCollectionFactory()
        valueset_collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()

        reference = CollectionReference(
            expression=valueset_collection.uri, collection=collection, valueset=[valueset_collection.uri]
        )
        reference.evaluate()
        reference.save()

        expansion.add_references(
            collection.references.all(), index=False, is_adding_all=True, force_reevaluate=True
        )

        self.assertEqual(expansion.concepts.count(), 0)
        self.assertEqual(expansion.mappings.count(), 0)


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


class CollectionReferenceTranslatorTest(OCLTestCase):
    def test_translate_cascade_as_dict_with_method(self):
        reference = CollectionReference(
            expression='/orgs/MyOrg/sources/MySource/concepts/c1/',
            code='c1',
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
            cascade={'method': SOURCE_TO_CONCEPTS}
        )
        self.assertEqual(
            reference.translation,
            'Include latest concept "c1" from MyOrg/MySource PLUS its mappings and their target concepts'
        )

    def test_translate_cascade_as_dict_without_method(self):
        reference = CollectionReference(
            expression='/orgs/MyOrg/sources/MySource/concepts/c1/',
            code='c1',
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
            cascade={'foo': 'bar'}
        )
        self.assertEqual(
            reference.translation,
            'Include latest concept "c1" from MyOrg/MySource'
        )

    def test_translate_is_static_transform(self):
        reference = CollectionReference(
            expression='/orgs/MyOrg/sources/MySource/concepts/c1/',
            code='c1',
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
            transform='resourceversions'
        )
        self.assertEqual(
            reference.translation,
            'Include latest latest version of concept "c1" from MyOrg/MySource'
        )

    def test_translate_filter_multiple_ampersand_joins(self):
        reference = CollectionReference(
            expression='/orgs/MyOrg/sources/MySource/concepts/',
            system='/orgs/MyOrg/sources/MySource/',
            reference_type='concepts',
            filter=[
                {'property': 'q', 'value': 'foo', 'op': '='},
                {'property': 'exact_match', 'value': 'true', 'op': '='},
                {'property': 'q', 'value': 'bar', 'op': '='},
            ]
        )
        self.assertEqual(
            reference.translation,
            'Include latest concepts from MyOrg/MySource containing "foo" '
            '& matching exactly with "true" & containing "bar"'
        )


class CollectionViewsAPITest(OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = UserProfile.objects.get(username='ocladmin')
        self.admin_token = self.admin.get_token()

    def test_verify_scope_no_kwargs_non_get_raises_404(self):
        response = self.client.post('/collections/', {}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_verify_scope_no_owner_scope_raises_404(self):
        response = self.client.get('/collections/some-collection/')
        self.assertEqual(response.status_code, 404)

    def test_collection_logo_view_get_permission(self):
        collection = OrganizationCollectionFactory()
        response = self.client.get(f'{collection.uri}logo/')
        self.assertIn(response.status_code, [200, 400, 404, 405])

    @patch('core.common.tasks.update_collection_active_mappings_count.apply_async')
    @patch('core.common.tasks.update_collection_active_concepts_count.apply_async')
    def test_get_object_updates_active_counts_when_not_test_mode(
            self, update_concepts_apply_async_mock, update_mappings_apply_async_mock):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        with override_settings(TEST_MODE=False):
            response = self.client.get(
                collection.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 200)
        update_concepts_apply_async_mock.assert_called()
        update_mappings_apply_async_mock.assert_called()

    def test_delete_collection_failure(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        with patch('core.collections.views.delete_collection') as delete_collection_mock:
            delete_collection_mock.return_value = False
            response = self.client.delete(
                collection.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 400)

    def test_reference_destroy_with_expansion(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        expansion = ExpansionFactory(collection_version=collection, created_by=self.admin)
        collection.expansion_uri = expansion.uri
        collection.save()
        concept = ConceptFactory()
        reference = CollectionReference.objects.create(
            collection=collection, expression=concept.uri, reference_type='concepts', created_by=self.admin
        )

        response = self.client.delete(
            f'{collection.uri}references/{reference.id}/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CollectionReference.objects.filter(id=reference.id).exists())

    def test_references_list_apply_filters(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        concept = ConceptFactory()
        CollectionReference.objects.create(
            collection=collection, expression=concept.uri, reference_type='concepts', created_by=self.admin,
            version='v1', cascade={'method': 'sourcetoconcepts'}, transform=TRANSFORM_TO_RESOURCE_VERSIONS
        )

        param_sets = [
            'empty=&repo_version=v1&versioning=unversioned&cascade=any&definition_type=intensional&'
            'inclusion_type=include&verbose=true',
            'versioning=repository&cascade=false&definition_type=extensional&inclusion_type=exclude',
            'versioning=resource&cascade=SourceToConcepts&definition_type=false',
            'cascade=sourcetoconcepts',
        ]
        for params in param_sets:
            response = self.client.get(
                f'{collection.uri}references/?{params}', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
            self.assertEqual(response.status_code, 200, params)

    def test_destroy_references_cascades_mappings(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(parent=source, from_concept=concept1, to_concept=concept2, map_type='Same As')
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        collection.add_expressions({'expressions': [concept1.uri]}, self.admin)

        response = self.client.delete(
            f'{collection.uri}references/?cascade=sourcemappings', {'expressions': [concept1.uri]},
            format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 204)

    def test_add_references_cascade_and_transform(self):
        source = OrganizationSourceFactory()
        concept1 = ConceptFactory(parent=source)
        concept2 = ConceptFactory(parent=source)
        MappingFactory(parent=source, from_concept=concept1, to_concept=concept2, map_type='Same As')
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)

        response = self.client.put(
            f'{collection.uri}references/?cascade=sourcetoconcepts&transformReferences=extensional',
            {'data': {'expressions': [concept1.uri]}}, format='json',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) >= 1)

    def test_add_references_collection_not_found(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        with patch(
                'core.tasks.mixins.TaskMixin.perform_task', return_value=([], {'error': 'Collection not found'})):
            response = self.client.put(
                f'{collection.uri}references/', {'data': {'expressions': ['/foo/']}}, format='json',
                HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'error': 'Collection not found'})

    def test_references_preview_verbose(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        concept = ConceptFactory()

        response = self.client.post(
            f'{collection.uri}references/preview/?verbose=true', {'data': {'expressions': [concept.uri]}},
            format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)

    def test_version_references_list(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        concept = ConceptFactory()
        CollectionReference.objects.create(
            collection=collection, expression=concept.uri, reference_type='concepts', created_by=self.admin
        )

        response = self.client.get(
            f'{collection.uri}HEAD/references/?verbose=true', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)

    def test_collection_versions_brief(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{collection.uri}versions/?brief=true', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_create_version_duplicate_id_conflict(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        payload = {'id': 'v1', 'released': False}

        first = self.client.post(
            f'{collection.uri}versions/', payload, format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            f'{collection.uri}versions/', payload, format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(second.status_code, 409)

    @patch('core.common.tasks.update_collection_active_mappings_count.apply_async')
    @patch('core.common.tasks.update_collection_active_concepts_count.apply_async')
    def test_version_get_object_updates_active_counts_when_not_test_mode(
            self, update_concepts_apply_async_mock, update_mappings_apply_async_mock):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with override_settings(TEST_MODE=False):
            response = self.client.get(
                version.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 200)
        update_concepts_apply_async_mock.assert_called()
        update_mappings_apply_async_mock.assert_called()

    def test_version_delete_failure(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with patch('core.collections.views.delete_collection') as delete_collection_mock:
            delete_collection_mock.return_value = False
            response = self.client.delete(
                version.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 400)

    def test_version_delete_already_queued(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        with patch(
                'core.tasks.mixins.TaskMixin.perform_task',
                return_value=Response({'detail': 'Already Queued'}, status=status.HTTP_409_CONFLICT)):
            response = self.client.delete(
                version.uri, HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )
        self.assertEqual(response.status_code, 409)

    def test_expansions_list_verbose(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{collection.uri}expansions/?verbose=true', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_version_expansion_not_found(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{collection.uri}HEAD/expansions/does-not-exist/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_expansion_re_evaluate_already_processing(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        expansion = ExpansionFactory(collection_version=collection, created_by=self.admin, is_processing=True)

        response = self.client.post(
            f'{collection.uri}HEAD/expansions/{expansion.mnemonic}/re-evaluate/', {},
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 409)

    def test_expansion_re_evaluate_starts_processing(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        expansion = ExpansionFactory(collection_version=collection, created_by=self.admin, is_processing=False)

        with patch('core.collections.views.seed_children_to_expansion'):
            response = self.client.post(
                f'{collection.uri}HEAD/expansions/{expansion.mnemonic}/re-evaluate/', {},
                HTTP_AUTHORIZATION=f"Token {self.admin_token}"
            )

        self.assertEqual(response.status_code, 204)

    def test_expansion_children_not_found(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{collection.uri}HEAD/expansions/does-not-exist/concepts/',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)

    def test_collection_summary_distribution(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get(
            f'{collection.uri}summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_collection_version_summary_distribution(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1',
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.get(
            f'{version.uri}summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_get_filter_params_resolves_latest_version(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1', released=True,
            created_by=self.admin, updated_by=self.admin
        )

        response = self.client.get(
            f'{collection.uri}latest/concepts/', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)

    def test_collection_list_staff_sees_all(self):
        OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        response = self.client.get('/collections/', HTTP_AUTHORIZATION=f"Token {self.admin_token}")
        self.assertEqual(response.status_code, 200)

    def test_collection_list_authenticated_non_staff_sees_own_private(self):
        user = UserProfileFactory(username='collections-view-user')
        OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin, public_access=ACCESS_TYPE_NONE)

        response = self.client.get('/collections/', HTTP_AUTHORIZATION=f"Token {user.get_token()}")

        self.assertEqual(response.status_code, 200)

    def test_version_update_records_released_event(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        version = OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1', released=False,
            created_by=self.admin, updated_by=self.admin
        )

        response = self.client.put(
            version.uri, {'released': True}, format='json', HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )

        self.assertEqual(response.status_code, 200)
        version.refresh_from_db()
        self.assertTrue(version.released)

    def test_collection_latest_version_summary_distribution(self):
        collection = OrganizationCollectionFactory(created_by=self.admin, updated_by=self.admin)
        OrganizationCollectionFactory(
            mnemonic=collection.mnemonic, organization=collection.organization, version='v1', released=True,
            created_by=self.admin, updated_by=self.admin
        )
        response = self.client.get(
            f'{collection.uri}latest/summary/?verbose=true&distribution=datatype',
            HTTP_AUTHORIZATION=f"Token {self.admin_token}"
        )
        self.assertEqual(response.status_code, 200)


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
            }
        )
        index_expansion_mappings_task_mock.apply_async.assert_called_once_with(
            (expansion.id,), task_id=ANY, queue='indexing')
